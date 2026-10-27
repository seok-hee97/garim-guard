# L1 학습 · 평가 재현

가림 L1(공유 인코더 + PII · 공격 · 보조 헤드)을 처음부터 다시 만드는 절차다. GPU 가 드는 단계(교사
3종 추론 · 학습)의 실행 기록과 실제로 겪은 문제는 [RUNPOD.md](RUNPOD.md), 결과는
[RESULTS_L1.md](RESULTS_L1.md) · [RESULTS_L1_R2.md](RESULTS_L1_R2.md) 에 있다. 학습된 가중치만 필요하면
다시 만들 필요 없이 Hugging Face `cycloevan/safeagent-kr` 에서 받는다(README).

## 0. 원칙

- **다운로드와 학습은 명시해야 시작한다.** 네트워크 다운로드와 optimizer 실행에는 각각 `--execute` 가
  필요하다. `JointL1Model.from_pretrained()` 도 명시적으로 바꾸지 않는 한 원격 다운로드를 허용하지 않는다.
- **자산은 revision 과 해시로 고정한다.** 모델 · 데이터 18종은 `train/models.yaml` · `train/sources.yaml` 이
  exact revision 과 라이선스를 고정하고, 다운로드 후 SHA-256 manifest 로 검증한다.
- **합성 코퍼스는 시드 결정적이다.** 다시 만들면 바이트가 같다 — 그래서 원격 머신에는 코드만 올리고 코퍼스는
  거기서 다시 만든 뒤 해시만 대조한다. 체크섬을 통과하는 합성 주민등록번호가 네트워크를 타지 않는다.
- **학습 입력과 평가 입력을 따로 봉인한다.** 평가 봉인은 학습 봉인과 정규화 지문 교집합이 비어 있어야
  성립하고, 봉인 뒤 입력 · 설정 · 자산이 하나라도 바뀌면 학습이 시작되지 않는다.

## 1. 환경과 자산

```bash
uv sync --extra train --extra dev          # Python 3.11
python train/preflight.py --stage implementation

python train/download_assets.py            # 계획만 출력 — revision · 저장 위치 확인
python train/download_assets.py --execute  # 검토된 revision 의 모델과 데이터만 받는다
python train/download_assets.py --record-existing   # 이미 받은 자산을 네트워크 없이 검증
```

## 2. 코퍼스

```bash
# 공개 값 사전 + 공개 코퍼스 + 로컬 합성 코퍼스. 체크섬 통과 난수는 실제 식별자와 우연히
# 일치할 수 있으므로 data/ 아래에만 둔다(Git 밖).
python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers --polyguard-limit 20000
```

deepset 영어 원문은 train/test 를 먼저 봉인한 뒤 번역했다. 번역 JSONL 은 각 행에 `id` · `text` ·
`method` 를 넣으며 ID 누락 · 추가 시 실패한다. LLM 번역은 비트 단위로 재생성되지 않으므로 결과 자체를
[`train/translations/`](../train/translations/)에 저장소와 함께 둔다(CC-BY-4.0 번역, 고지는 NOTICE).
현재 번역본은 Gemini 50행 + Claude 612행이고, 한도에 막혀 나머지를 Claude 로 옮긴 사실이 행마다
`method` 에 남아 있다. 새로 만들 때:

```bash
python train/translate_with_gemini.py --model gemini-3.8-flash --batch-size 50 --no-row-retry \
  --queue data/corpus/deepset_train_queue-train.jsonl --output train/translations/deepset-train-ko.jsonl
```

번역 적용(`apply_translations.py`)은 파이프라인 2c 단계가 한다.

## 3. 교사 → 합의 → 봉인 → 학습 — `runpod_pipeline.py` 한 명령

PII 병합 → 교사 풀 → 교사 3종 → 합의 → 학습 · 평가 봉인 → preflight → 학습은
[`train/runpod_pipeline.py`](../train/runpod_pipeline.py) 하나가 유일한 출처다. 차단 목록과 입력 조합이
문서 여러 곳에 따로 적혀 있다가 어긋났기 때문이다. 단계표는 [RUNPOD.md](RUNPOD.md).

```bash
# GPU 없이 전체 배선을 먼저 검증한다 — 교사 대역, 산출물은 train/artifacts/rehearsal/, 학습은 dry-run
python train/runpod_pipeline.py --rehearse

# CUDA 머신에서 — 교사 3종부터 학습 dry-run 까지, 그리고 3-seed 학습
python train/runpod_pipeline.py --device cuda
python train/runpod_pipeline.py --device cuda --train
```

지키는 것:

- **최종 평가셋**과 정규화 지문이 같은 문장은 학습 입력에서 뺀다. 평가 쪽은 건드리지 않는다.
- **2d 데이터 감사** — 꼬리말 · 가역 은닉을 걷어 낸 기본 문장 단위로 test 와 학습의 중복 · 근접 중복 ·
  라벨 충돌 · 정상 문장 다양성을 본다. R1 합성을 넣으면 멈춘다(RESULTS_L1 §5-③).
- 공격 학습 라벨은 **교사 합의**로만 붙는다. 생성기 라벨은 사이드카에 두고, 생성기 정상 행에 한해 세
  번째 표로 쓴다(PREREG_L1_R2 C3). 자작 공격이 교사 검증을 우회하지 않는다.
- 교사 불일치 · 누락은 `NONE` 에 흡수하지 않는다. 기본 운영(`--drop-review`)은 감사 기록과 함께 폐기한다.
- **7b 합의 감사** — 생성기 정상 채택률이 0.9 아래면 멈춘다.

사람 검수를 하는 운영이라면 7단계를 `--drop-review` 없이 돌린 뒤 블라인드 큐로 라벨링하고,
`attack-human-reviewed-{train,validation}.jsonl` 을 8단계 공격 입력에 추가한다.
[라벨링 가이드](LABELING_GUIDE.md)를 먼저 고정하고 교사 예측을 보지 않은 채 라벨한다.

```bash
python train/create_labeling_queue.py --purpose teacher-review \
  --input data/corpus/resolved/attack-teacher-review.jsonl \
  --output data/local/teacher-review-blind.jsonl
python train/apply_review_labels.py \
  --queue data/corpus/resolved/attack-teacher-review.jsonl \
  --labels data/local/teacher-review-labels.jsonl \
  --output-dir data/corpus/resolved
```

## 4. 내보내기 · 평가 · 판정

seed 는 검증 `best_joint_recall_hmean`(PII char-recall 과 공격 macro-recall 의 조화평균) 최댓값으로
고른다(동률이면 앞 seed). 내보내기는 FP32 와 INT8(QInt8 per-channel · `reduce_range`)을 같이 만든다.

```bash
S=<고른 seed>
python train/export_l1.py --encoder-dir models/pinned/l1_ax_encoder \
  --checkpoint train/artifacts/l1/seed-$S/joint_l1.pt --output-dir train/artifacts/l1-onnx
python train/evaluate_l1.py --model-dir train/artifacts/l1-onnx --baseline-model-file garim_l1.fp32.onnx \
  --evaluation-manifest data/evaluation-input-manifest.json --output train/artifacts/l1-eval.json \
  --input data/corpus/<봉인된 test 파일 전부> ...
python -m bench.l1_product_path --l1-model-dir train/artifacts/l1-onnx --input ... --output ...
python -m bench.run_axis_b --benchmark data/raw/KoreanGuardrail/benchmark.jsonl --judge garim-l1 \
  --l1-model-dir train/artifacts/l1-onnx --output train/artifacts/axis-b-l1.json
python train/benchmark_l1.py --model-dir train/artifacts/l1-onnx --output train/artifacts/l1-bench.json
```

- `evaluate_l1.py` 는 `split=test` 가 아닌 행, 번역 · 교사 · 수동 라벨 미해결 flag, 공격 gold 누락을
  거부하고, 평가 봉인의 SHA-256 과 학습 입력과의 지문 교집합을 다시 검사한다. `--input` 은 봉인한 파일
  **전부**여야 한다. 이 수치는 임계 없는 L1 원출력이다 — 제품이 실제로 가리는 수치는
  `bench/l1_product_path.py`(원출력 · 런타임 임계 · 제품 경로 셋으로 나눠 잰다).
- 2차 학습처럼 사전 등록한 판정이 있으면 `bench/r2_eval.py`(새 홀드아웃, family 단위 bootstrap) ·
  `bench/mcnemar_axis_b.py`(행별 짝 검정) · `bench/r2_verdict.py`(등록된 통과선)로 낸다.
- **판정 측정은 R1 과 R2 를 같은 기계 · 같은 세션에서 잰다.** 제품 경로 오탐은 임계 바로 위 개체가
  CPU 따라 갈린다.
- 지연(`benchmark_l1.py`)은 노트북 기준으로 다시 잰다 — 컨테이너 CPU 값은 기준이 아니다.

## 5. 겪은 함정 — 다음 실행 전에 읽을 것

| 함정 | 지금의 조치 |
|---|---|
| 생성기 `template_id` 가 인덱스 나머지라 test 기본 문장이 학습에도 있었다 | family 단위 분할 + 2d 데이터 감사 |
| 2표 합의가 정상 하드 네거티브를 통째로 지웠다 | 생성기 정상 행에 한해 생성기 표(C3) + 7b 감사 |
| 꼬리말 번호가 라벨 구간을 알려 줬다 | 라벨과 무관한 난수 순열 |
| INT8 이 VNNI 없는 x86 에서 int16 포화로 깨졌다 | `quantize_joint_onnx` 가 `reduce_range=True` |
| onnxruntime 이 컨테이너 CPU 할당량을 넘는 스레드를 잡았다 | `OnnxL1Backend` 가 `GARIM_L1_THREADS` → cgroup 할당량 순으로 스레드 결정 |
| HF 업로드가 Xet 경로에서 30KB/s 였다 | `HF_HUB_DISABLE_XET=1`(LFS) — 같은 회선에서 844KB/s |

`tests/test_training_onnx_roundtrip.py` 는 고정 자산 없이 작은 인코더 · 토크나이저로 torch 모델 → ONNX →
QInt8 → `OnnxL1Backend` 전 구간을 검증한다. 계약 검증이며 정확도 · 지연 측정이 아니다.
