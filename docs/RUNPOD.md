# RunPod 실행 절차

로컬(M1 / 16GB)에서 못 하는 것만 여기서 한다 — **교사 라벨링**과 **R1/R2 학습**이다.
왜 못 하는지는 실측으로 확인했다.

| | 로컬 | 이유 |
|---|---|---|
| kanana 2.1b 추론 | ✅ | 4.19GB, MPS 로 돈다 |
| gemma-4-E2B 추론 | ❌ | 10.28GB 를 16GB 에서 돌리면 스왑 — 4행에 10분 초과 |
| Qwen3Guard-**Gen** 4B | ❌ | Stream → Gen-0.6B → **Gen-4B** — 아래 참조. bf16 약 9GB |
| R1/R2 gradient 학습 | ❌ | MPS 가 matmul 에서 죽는다. CUDA 가 필요하다 |

## ★ R2 — 2차 학습 (2026-10-27 실행 완료 · 불채택 — [RESULTS_L1_R2.md](RESULTS_L1_R2.md))

무엇을 바꾸고 무엇으로 판정하는지는 [PREREG_L1_R2.md](PREREG_L1_R2.md) 에 학습 전에 고정했다.
`runpod_pipeline.py` 는 이제 R2 경로다(R1 은 커밋 `571bb64`). 아래 R1 절차와 다른 점만 적는다.

**올리는 것이 둘 늘었다** — 저장소 밖 파일이지만 재생성하려면 GPU 가 다시 든다.

| 로컬 → pod 같은 경로 | 왜 |
|---|---|
| `data/runpod/2026-10-27/corpus/attack-teacher-input.jsonl` · `teacher-{kanana,gemma4,qwen3guard_gen}.jsonl` | 6a 가 R1 교사 산출물 중 **ID 와 텍스트 sha256 이 둘 다 같은** 행을 재사용한다 — 17,734행(PolyGuardMix 17,220 · deepset 514). 교사를 새로 돌리는 것은 R2 합성 2,466행뿐이다. 교사 산출물에는 텍스트가 없고, 입력 파일은 공개 데이터와 식별자 없는 합성 공격문이다 |
| `train/artifacts/l1-onnx/` 의 `garim_l1.int8.onnx` · `labels.json` · `tokenizer.json` · `tokenizer_config.json` | PREREG §5 — R1 을 R2 와 **같은 기계·같은 세션**에서 다시 잰 값이 판정 기준이다 |

```
1   python train/download_assets.py --execute
2   python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers --polyguard-limit 20000
    (위 두 묶음을 scp)
3~10 python train/runpod_pipeline.py --device cuda --train
```

대조용 해시(로컬 리허설, 2026-10-27) — 안 맞으면 학습하지 않는다. 새 평가 셋 넷의 해시는
PREREG §9 에 있다.

```
5bb694f5a1c04e247eed25aca8debf7a3563a42cb4166488a8587df6babb3720  data/merged/pii-train.jsonl
66a12b0f2050e1b64a3f8b6cf31e946e251ccae7dcd9c78aeaa5f28ae4f2fb10  data/merged/pii-validation.jsonl
83d39963a621603725de4d39a206771674f7d257fa536ec88633b66841e5e0f6  data/corpus/attack-teacher-input-r2.jsonl
```

로컬 리허설은 2b~7b 를 그대로, 8~10 은 Qwen 항목을 뺀 임시 models manifest 로 통과했다(로컬 Qwen
자산이 0.6B 라 8단계 자산 drift 검사에 걸린다 — R1 때와 같은 로컬 전용 실패). 2d 데이터 감사 · 7b 합의
감사 통과, 평가 봉인 9개 파일 17,042행, preflight ok, 학습 dry-run 64창.

**11 — R2 판정.** seed 는 `train/artifacts/l1-r2/three-seed-report.json` 에서 같은 규칙으로 고른다.
R1(`l1-onnx`)과 R2(`l1-r2-onnx`)를 **같은 명령으로 둘 다** 잰다.

```
S=<고른 seed>
python train/export_l1.py --encoder-dir models/pinned/l1_ax_encoder \
  --checkpoint train/artifacts/l1-r2/seed-$S/joint_l1.pt --output-dir train/artifacts/l1-r2-onnx
python train/evaluate_l1.py --model-dir train/artifacts/l1-r2-onnx --baseline-model-file garim_l1.fp32.onnx \
  --evaluation-manifest data/evaluation-input-manifest-r2.json --output train/artifacts/r2/l1-eval.json \
  $(for f in pii-synthetic-test attack-synthetic-r2-test t4-r2-test t4-prime-r2-test attack-external-gold-test \
      kdpii-test klue_eval-test bc_pii_augmented_negative-test pii-negative-r2-test; \
    do printf -- '--input data/corpus/%s.jsonl ' $f; done)
for M in l1-onnx l1-r2-onnx; do
  python -m bench.r2_eval --l1-model-dir train/artifacts/$M --output train/artifacts/r2/$M-r2-eval.json   # O2·O3·O4·S4
  python -m bench.l1_product_path --l1-model-dir train/artifacts/$M --output train/artifacts/r2/$M-product-path.json \
    --input data/corpus/kdpii-test.jsonl --input data/corpus/klue_eval-test.jsonl \
    --input data/corpus/bc_pii_augmented_negative-test.jsonl                                              # O3·O4
  python -m bench.run_axis_b --benchmark data/raw/KoreanGuardrail/benchmark.jsonl --judge garim-l1 \
    --l1-model-dir train/artifacts/$M --output train/artifacts/r2/$M-axis-b.json                          # O1·S1
  python -m bench.run_axis_b --benchmark data/raw/KoreanGuardrail/benchmark.jsonl --sample 600 --judge garim-l1 \
    --l1-model-dir train/artifacts/$M --output train/artifacts/r2/$M-axis-b-600.json                      # S5
done
python train/benchmark_l1.py --model-dir train/artifacts/l1-r2-onnx --output train/artifacts/r2/l1-bench.json
```

가져올 것: `train/artifacts/l1-r2/` · `l1-r2-onnx/` · `r2/`, `data/training-input-manifest-r2.json` ·
`data/evaluation-input-manifest-r2.json`, `data/corpus/teacher-*-r2.jsonl` · `data/corpus/resolved-r2/`,
`data/teacher_agreement-r2.json`, pod 로그.

### 실제 실행에서 — 위 11단계대로 가면 안 되는 이유 둘 (2026-10-27)

3~10 은 위 절차 그대로 통과했다(해시 게이트 15개 일치 · 교사 재사용 17,734행 · 학습 약 3시간 20분).
**11 에서 둘이 터졌고, 둘 다 코드에 반영해 다음부터는 위 명령 그대로 된다.**

| # | 증상 | 원인 → 조치 |
|---|---|---|
| 1 | 첫 평가가 2시간 넘게 INT8 한 번을 못 끝냈다 | pod 의 cgroup CPU 할당량이 8.5코어인데 onnxruntime 이 보이는 물리 코어(32)만큼 스레드를 잡아 throttling. 346.9 → 35.7 ms/행(출력 동일). `OnnxL1Backend` 가 `GARIM_L1_THREADS` → 할당량 순으로 스레드를 정한다(`b540adf`). 확인: `cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us` |
| 2 | R1 을 다시 재니 Mac 기준선과 크게 달랐다(계좌 짝 R 1.0 → 0.404) | pod CPU(EPYC 7452)가 AVX2 만 있고 VNNI 가 없어 INT8 U8S8 곱이 int16 에서 포화. `quantize_joint_onnx` 가 `reduce_range=True` 로 양자화한다(`2158d84`) — 이제 `export_l1.py` 출력이 곧 그 판이다. 확인: `grep -o -w 'avx512_vnni\|avx_vnni' /proc/cpuinfo` |

R2 판정은 R1 · R2 를 각자의 FP32 에서 `reduce_range` 로 다시 양자화해 같은 pod 에서 쟀다(PREREG §8 마지막
행). 실제로 돈 스크립트는 `data/runpod/2026-10-27-r2/final/logs/step11c-r2.sh` 이고, INT8 · FP32 대조와
지연 벤치는 판정 지표를 먼저 내려고 뺐다.

- **이 pod 는 드라이버가 CUDA 13.0** 이라 lock 의 cu13 torch 가 그대로 GPU 를 봤다 — 아래 "CUDA 12.8 pod"
  의 torch 교체가 필요 없었다(`uv sync --python 3.11 --extra train --extra dev`).
- **pod → Mac 다운로드 약 0.8MB/s, Mac → pod 업로드는 다운로드와 겹치면 단일 연결 20KB/s.** 152MB 를
  8조각으로 나눠 병렬 `scp` 하니 약 190KB/s 였다. 큰 파일은 pod 에서 만들 수 있으면 pod 에서 만든다
  (양자화는 기계가 달라도 바이트가 같다).
- 회수물은 Mac `data/runpod/2026-10-27-r2/`(sha256 대조), 모델 · 데이터는 HF `cycloevan/garim-guard`(model · dataset,
  합성 주민번호 파일과 원천은 비공개 `cycloevan/garim-guard-private`)에 있다.

## ★ 올리는 것은 저장소뿐이다

코퍼스도 모델도 올리지 않는다. 둘 다 **재생성·재다운로드가 결정적**이기 때문이다.

예외가 하나 있고, 그래서 그것은 **저장소에 넣었다** — deepset 한국어 번역본
(`train/translations/`). LLM 번역은 재실행해도 비트 단위로 같지 않으므로, 결과 자체를
저장소에 두어야 이 원칙이 유지된다. 공개 CC-BY-4.0 데이터의 번역이라 개인정보가 없고
고지는 NOTICE 에 있다.

- 자산 18종은 `train/sources.yaml`·`train/models.yaml` 이 revision 을 고정한다.
  `download_assets.py --execute` 가 같은 것을 받는다.
- 합성 코퍼스는 시드 결정적이다. **재생성본이 로컬본과 비트 단위로 같은 것을 확인했다**
  (아래 해시). 그래서 원격에서 만들고 해시만 대조하면 동일성이 증명된다.

이 방식의 이득이 하나 더 있다. 합성 코퍼스에는 **체크섬을 통과하는 주민등록번호**가 들어
있다(`--ack-sensitive-synthetic-identifiers` 게이트가 그것 때문에 있다). 재생성 방식이면
그 값들이 **네트워크를 타지 않는다.**

### 대조용 해시 (로컬 기준, 2026-10-27 — pod 와 일치 확인)

`runpod_pipeline.py` 가 3·5c 단계에서 이 값을 출력한다.

```
0d62d35e1fad4ba9e45f247dae2098abe7ad5ed939bc858d46bc161d763ccfe4  data/merged/pii-train.jsonl
213d9685b3ca7885a62d4b3b17bbc9942a883dd40c7f5bdce618b00482e8a002  data/merged/pii-validation.jsonl
fc305d710e93efbd4dd59b49e1d8b792de6f1b2f1782f19fb468c62c456f4b9d  data/corpus/attack-teacher-input.jsonl
34f9d7ac891fe1fb96476345bff8818d1b81899bf0768ae868c2f05dcaa4e48c  data/corpus/t4-test.jsonl
1cd1d529f5ab6b4abd2a1d18770fe5f1c1921ee176f4b0bafca12bf84bef40db  data/corpus/t4-prime-test.jsonl
```

2단계(`prepare_corpora.py`) 산출 16개도 2026-10-26 원자산에서 다시 만들어 로컬본과 **바이트
단위로 같은 것**을 확인했다. 2026-10-27 KDPII span 공백 수정으로 `kdpii-train`(`7b8aa4a0…`)·
`kdpii-validation`(`4e14aa55…`)과 위 PII 병합 두 값이 바뀌었다. `kdpii-test` 는 그대로다.

(이전 판의 `attack-train/validation.jsonl` 해시는 합성 1,238행짜리 옛 봉인 입력이라 뺐다.
공격 학습 입력은 이제 7단계 교사 합의 산출물이다. PII 두 값은 3단계에 평가 차단 목록이
들어가며 바뀌었다 — 아래 "리허설이 찾은 것".)

⚠️ 이 값이 안 맞으면 **학습을 시작하지 않는다.** 원격이 다른 데이터를 보고 있다는 뜻이고,
그때 나오는 지표는 로컬에서 잰 어떤 것과도 비교할 수 없다.

## 환경

- **Python 3.11** — R1 · R2 는 3.11 에서 돌았고 재현도 3.11 로 한다. 실행 당시 `requires-python` 은
  `">=3.11,<3.12"` 여서 3.12 이미지는 `uv sync` 가 거부했다(2026-10-28 부터 런타임은 3.11 ~ 3.14 허용).
- **드라이버가 CUDA 13 을 지원하는 pod** — `uv.lock` 의 torch 2.13.0 은 `manylinux_2_28_x86_64`
  휠이고 CUDA 13 런타임(`nvidia-*-cu13`)을 끌고 온다. pod 의 `nvidia-smi` 가 `CUDA Version`
  13.0 이상을 보여야 한다. RunPod 생성 화면의 CUDA 버전 필터로 고른다. 아니면 torch 가 GPU 를
  못 보고, `runpod_pipeline.py` 의 0단계(CUDA 확인)가 바로 멈춘다.
- **GPU — RTX 3090 / 4090(24GB) 둘 다 된다.** 둘 다 bf16 과 CUDA 13(Ampere sm_86 · Ada sm_89)을
  지원한다. 4090 이 bf16 처리량이 더 커서 빠르고, 3090 도 결과는 같다. 24GB 에 맞추려고 고친 것:
  - **교사 로짓** — 배치 전체 위치의 로짓(배치 × 길이 × 어휘)을 만들던 것을 마지막 실토큰
    위치에만 걸도록 바꿨다. 실제 Qwen 에서 값이 같은 것을 확인했다(max|Δlogit| 0).
  - **교사 입력 길이 상한 2,048토큰** — 최장 행이 약 4만 토큰이었다. kanana 문맥(8,192)을 넘는 행이
    있었고, 그 행이 든 배치는 어떤 GPU 에서도 죽는다. 38행(0.2%)을 감사 파일로 뺀다.
  - **학습** — 논리 배치 32 는 그대로, 16행씩 두 번 기울기를 쌓고(`micro_batch_size: 16`) CUDA 에서
    bf16 autocast(`precision: bf16`, master weight fp32). fp32 · 32행 그대로였다면 24GB 근거가 없었다.
  - **0b 메모리 프로브** — 교사보다 먼저 학습 최악 배치(32행 전부 512토큰)를 GPU 에서 돌려 최대
    메모리를 잰다. VRAM 의 85% 를 넘으면 `micro_batch_size` 를 줄이라고 하고 멈춘다.
- **디스크 60GB 이상** — 고정 자산 약 17GB(교사 3종 15GB · 원천 데이터 2.5GB) + venv(torch·CUDA
  라이브러리) + 코퍼스·체크포인트·ONNX.
- `HF_TOKEN` 이 필요하다(gated 아닌 저장소도 rate limit 때문에 있는 편이 낫다).
- `main` 이 가림이다(2026-10-28 부터 — 이전 프로젝트는 `legacy-safeagent` 브랜치). 공개 저장소라 그냥
  clone 한다. 비공개 포크라면 pod 에 자격 증명을 남기지 않도록 **git bundle** 로 올린다(아래 "CUDA 12.8
  pod" 절).

```
pip install uv
git clone https://github.com/seok-hee97/garim-guard.git && cd garim-guard
uv sync --python 3.11 --extra train --extra dev
export HF_TOKEN=...
nvidia-smi        # CUDA Version >= 13.0 인지 확인
```

### 실제 실행에서 — 드라이버가 CUDA 12.8 인 pod (2026-10-27)

받은 RTX 3090 pod 는 드라이버가 CUDA 12.8 이었다. lock 의 cu13 torch 로는 GPU 를 못 본다. 그래서:

- `uv sync --extra train` 뒤 torch 만 **cu126 휠로 바꿨다** — `torch==2.13.0+cu126`,
  `torchvision==0.28.0+cu126`, 인덱스는 `https://download.pytorch.org/whl/cu126` **하나만**(PyPI 를
  같이 주면 PyPI 휠을 먼저 집는다). 느린 망에서는 `UV_HTTP_TIMEOUT=300`.
- cu13 `nvidia-*` 패키지를 지우지 않는다. cu12 패키지와 **파일을 공유**해서(`libcusparseLt` 등)
  지우면 torch 가 깨진다. 깨졌으면 cu126 쪽을 `--reinstall` 한다.
- 이후 **`uv run` 을 쓰지 않는다** — 실행할 때마다 lock 으로 다시 맞춰 cu13 torch 로 되돌린다.
  `.venv/bin/python` 을 직접 부른다.
- 저장소는 토큰 대신 **git bundle** 로 올렸다(`git bundle create … <base>..<branch>` → `scp` →
  `git fetch <bundle> <branch>`). pod 에 자격 증명이 남지 않는다.
- 디스크는 50GB 오버레이로 충분했다 — 최대 34GB(모델 22GB · venv 6.6GB · 데이터 2.6GB · 학습 산출물 2.5GB).
- 긴 단계는 `tmux` 안에서 돌린다. 11단계는 `PIPELINE_EXIT=0` 을 기다렸다가 자동으로 이어 붙였다.

## 순서

```
1  python train/download_assets.py --execute
2  python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers --polyguard-limit 20000
3~10  python train/runpod_pipeline.py --device cuda            ← 학습 dry-run 까지
      python train/runpod_pipeline.py --device cuda --train    ← 3-seed 학습까지
11 python train/export_l1.py  →  python train/evaluate_l1.py  →  python train/benchmark_l1.py
```

`runpod_pipeline.py` 가 단계의 **유일한 출처**다. 안에서 하는 일:

| 단계 | 내용 |
|---|---|
| 0·0b | CUDA 확인 → 학습 최악 배치 GPU 메모리 프로브(85% 넘으면 멈춤) |
| 3 | PII 병합 — **최종 평가셋 지문을 학습에서 뺀다**. 해시 출력 → 위 표와 대조 |
| 2b·2c | T4·T4′ 생성, deepset 번역 적용(`train/translations/`)·외부 이진 gold — 3단계 차단 목록이 이 파일들을 읽는다 |
| 5 | 생성기 라벨 분리(사이드카 보관) |
| 5b·5c | 공격 교사 풀 — 평가셋 지문과 **PII 학습 문장**을 뺀다. train+validation 을 한 입력으로, kanana 기준 2,048토큰 초과 행은 `attack-teacher-overlength.jsonl` 로 뺀다 |
| 6 | 교사 3종 — `run_teacher.py` 가 이어 돌기를 지원하므로 끊기면 같은 명령을 다시 부른다 |
| 6b | 일치도·JAILBREAK 독립성 — 동의율 ≥ 0.95 면 멈춘다(`--accept-rubber-stamp` 로만 진행) |
| 7 | 합의(`--drop-review`) — 합의된 행 전체에서 **성분 단위**로 검증 10% 를 떼어 낸다 |
| 8·8b | 학습 봉인 → 평가 봉인(학습 봉인 해시 재검증 + 지문 교집합 ∅) |
| 9·10 | preflight training → 학습 dry-run → (`--train`) 학습 |

### 11 — 학습 뒤: seed 선택 · 내보내기 · 평가 · 회수

seed 선택은 자동이 아니다. `train/artifacts/l1/three-seed-report.json` 의 seed 별
`best_joint_recall_hmean`(검증 PII char-recall 과 공격 macro-recall 의 조화평균)이 가장 높은
seed 를 고르고, **고른 이유를 모델 카드에 적는다**(`train/MODEL_CARD_TEMPLATE.md`).

```
S=<고른 seed>
python train/export_l1.py --encoder-dir models/pinned/l1_ax_encoder \
  --checkpoint train/artifacts/l1/seed-$S/joint_l1.pt --output-dir train/artifacts/l1-onnx
python train/evaluate_l1.py --model-dir train/artifacts/l1-onnx \
  --baseline-model-file garim_l1.fp32.onnx --output train/artifacts/l1-eval.json \
  $(for f in pii-synthetic-test attack-synthetic-test t4-test t4-prime-test \
      attack-external-gold-test kdpii-test klue_eval-test bc_pii_augmented_negative-test; \
    do printf -- '--input data/corpus/%s.jsonl ' $f; done)
python train/benchmark_l1.py --model-dir train/artifacts/l1-onnx --output train/artifacts/l1-bench.json
```

`evaluate_l1.py` 의 `--input` 은 8b 에서 봉인한 여덟 파일 **전부**여야 한다 — 봉인과 다르면 거부한다.

**0b 프로브가 멈추거나 학습이 OOM 으로 죽으면** `train/training.yaml` 의 `micro_batch_size` 를
절반으로 줄이고 같은 명령을 다시 부른다. `batch_size`(논리 배치 32)는 줄이지 않는다 — 그건 학습
설정을 바꾸는 일이다. 교사 단계는 이어 돌기라 이미 끝난 행을 건너뛰고, 봉인(8·8b)은 바뀐 설정으로
다시 만들어진다. 학습 VRAM 은 로컬에서 잴 수 없어서(Mac 은 CUDA 가 없고 swap 이 거의 차 RSS 도
무의미했다) 0b 가 pod 에서 교사보다 먼저 잰다.
`benchmark_l1.py` 의 지연은 pod CPU 값이라 §7.1.3 의 노트북 기준과 다르다. 지연은 로컬에서
다시 잰다.

**pod 를 끄기 전에 가져올 것** — pod 디스크는 남지 않을 수 있다.

| 경로 | 무엇 |
|---|---|
| `train/artifacts/l1/` | seed 별 체크포인트·학습 보고서, `three-seed-report.json` |
| `train/artifacts/l1-onnx/` | FP32·QInt8 ONNX, `labels.json`, 토크나이저 — 런타임 `--l1-model-dir` 로 바로 쓴다 |
| `train/artifacts/l1-eval.json` · `l1-bench.json` | 평가·지연 |
| `data/training-input-manifest.json` · `data/evaluation-input-manifest.json` | 무엇으로 학습·평가했는가(봉인) |
| `data/corpus/teacher-*.jsonl` · `data/corpus/resolved/` | 교사 라벨 원본·합의·폐기 감사(재학습 시 교사를 다시 안 돌려도 된다) |
| `data/teacher_agreement.json` | 6b 일치도·JAILBREAK 독립성 |

⚠️ `data/` 에는 체크섬을 통과하는 합성 주민등록번호가 있다. 가져온 것도 Git 밖에 둔다.

### ★ 먼저 로컬에서 리허설한다 — GPU 없이 2b~10 배선 전체

```
python train/runpod_pipeline.py --rehearse
```

교사 3종 대신 **교사 대역**(형식·ID 범위·투표 구조만 같고 라벨은 지어낸 것,
`prompt_version: rehearsal-standin`)을 만들고 나머지를 실제 CLI 로 돈다. 교사·합의·봉인
산출물은 Git 밖 `train/artifacts/rehearsal/` 에만 생기고 학습은 dry-run 이다. M1 에서 수 분,
모델은 A.X 인코더 dry-run 하나만 올린다. **RunPod 을 켜기 전에 매번 돌린다.**

### 리허설이 찾은 것 — 교사 실행 뒤 단계의 결함 다섯 (2026-10-26)

전부 GPU 교사를 다 돌린 **뒤에야** 드러날 자리였다.

| # | 단계 | 증상 | 원인 → 조치 |
|---|---|---|---|
| 1 | 6b | 기본 `--gold` 파일 없음 · `extra=17,787` | 사이드카가 분할별 두 파일로 갈렸고(13e09b8), 하네스가 교사 산출물이 gold 와 **정확히** 같은 ID 이기를 요구했다 → 기본값 두 파일, gold ⊆ 교사 산출물·교사끼리 같은 ID 집합 요구 |
| 2 | 7 | `template 'benign-076' crosses dataset splits` | 버킷을 `group or template` 하나로 정해 group 여럿에 걸친 template 이 갈렸다(합성 300개 전부) → union-find 성분 단위 분할 |
| 3 | 7 | 검증 분할의 PolyGuard 0행 | PolyGuardMix `group_id` 가 하위 데이터셋 이름이라 17,273행이 group 둘로 뭉쳤다 — 해시가 반대로 떨어졌다면 공격 데이터 84% 가 검증으로 빠졌다 → 행 단위 group |
| 4 | 8 | `fingerprint crosses validation/train` | `네, 맞습니다.` 류가 KDPII(양 분할)와 PolyGuard 에 같이 있었다 → 공격 풀에서 PII 학습 문장 제거(`--blocked-other-task`) |
| 5 | 8b | `evaluation … overlap training` | PII 병합에 평가 차단 목록이 없었다 — KDPII test 일반 발화, KLUE train·validation 의 라벨까지 같은 문장 → 3단계에서 학습 쪽을 제거. **봉인 규칙은 그대로 엄격하다** |

> 2026-10-26 로컬 상태: 1~5c 완료, 리허설 통과. **로컬에서 교사 둘(kanana·Qwen)만 먼저
> 돌릴 이유가 없다** — 정족수 2표에서 kanana 는 NONE/INJECTION/SYSTEM_LEAK, Qwen 은
> JAILBREAK 만 투표하므로 Gemma 없이는 어떤 행도 합의되지 않는다. M1 에서 kanana 는 약
> 45행/분(다른 작업과 CPU·메모리를 나눠 쓴 상태의 실측)이라 19k 에 7시간 안팎이다.

### 공격 교사 풀 — PolyGuardMix 샘플링

어댑터가 언어를 `"ko"` 로 걸러서 **PolyGuard 한국어를 한 행도 수확하지 못하고 있었다**
(상류는 `"Korean"` 이라고 적는다). 92,740행이 통째로 버려졌고, 그것이 공격 학습 풀을
1,238행으로 묶어 둔 원인이었다.

전량을 쓰면 교사 3종 추론이 278k forward 라 §6 의 R2 추정(~1,920)을 크게 넘는다.
**20,000행을 결정적으로 샘플링**한다 — 레코드 id 의 안정 해시 순위로 뽑으므로 parquet
glob 순서가 달라져도 같은 집합이 나오고, 원천이 커져도 대부분 유지된다.

    PolyGuardMix 한국어   92,710  (평가 지문 중복 30 제거 후)
    → 20,000 샘플         merge 에서 중복 2,727 제거
    → 최종 교사 풀        18,511  (PolyGuard 17,273 + 합성 1,238)

    PII 108,588 : 공격 18,511 = 5.9:1   (고치기 전 88:1)
    pii_per_attack=4 기준 업샘플 1.5배   (이전 21.7배)

**`pii_per_attack` 결정이 이것으로 해소됐다.** 공격 1행을 5에폭에 109번 보던 것이
5~6번이 된다.

### 8b — 평가 봉인은 왜 따로인가

학습 입력만 봉인하면 "무엇으로 학습했는가" 는 고정되지만 **"무엇으로 평가했는가" 는
열려 있다.** 평가 코퍼스를 나중에 바꾸면 지표가 조용히 달라지고, 더 나쁘게는 학습에
쓴 행이 평가로 새어 들어와도 아무도 모른다.

`finalize_evaluation_inputs.py` 는 봉인하기 전에 **학습 매니페스트의 해시를 먼저
재검증**하고(schema 2 · 내용 drift · 레코드 수 drift), 그 다음 **정규화 지문 교집합이
∅ 인지** 확인한다. 낡거나 손댄 학습 매니페스트로는 통과할 수 없다.

실제로 강제되는 것을 확인했다 — 학습 20행을 `split=test` 로 바꿔 평가로 봉인하려 하니
위반 id 를 짚어 거부했다.

    ValueError: evaluation text fingerprints overlap training inputs:
        ['syn-attack-0056', 'syn-attack-0099', ...]

처음 설계한 봉인 대상은 넷이었다 — 합성 PII test · 합성 공격 test · **T4 2,800** · **T4′ 400**
(총 5,872행). 실제 R1 봉인은 외부 셋 넷(공격 외부 gold · KDPII · KLUE · bc 음성)을 더한 **여덟 파일
17,146행**이다(11단계 명령, RESULTS_L1).

⚠️ **8번(봉인)이 5~7번 뒤에 온다.** 교사 라벨이 붙은 코퍼스를 봉인해야 한다. 봉인은
`train/training.yaml` 까지 덮으므로 하이퍼파라미터를 바꾸면 **재봉인**이다 — 바꾸고
안 하면 `training input path drift` 로 막힌다.

## 교사 교체 — Stream → Gen (해소됨)

`Qwen/Qwen3Guard-Stream-0.6B` 의 원격 코드가 transformers 4.x 를 벤더링한 것이라 우리
pin(`>=5.5,<6`)에서 안 돈다. `config.pad_token_id` 를 채워 넘겼더니 8줄 뒤
`ROPE_INIT_FUNCTIONS['default']` 로 또 막혔고, 그 파일은 transformers 내부 14개 모듈을
임포트한다. 상류가 안 고쳤다(고정본 = 현재 main). **하드웨어 무관이라 여기서도 같다.**

뺄 수는 없었다 — `minimum_votes: 2` 인데 JAILBREAK 투표자가 gemma4 와 이 교사 둘뿐이라,
빼면 그 클래스가 정족수를 영영 못 채우고 전부 폐기된다.

**`Qwen/Qwen3Guard-Gen-0.6B` 로 교체했다.** 원격 코드가 없어 표준 클래스로 로드되고,
같은 계열·같은 택소노미라 `votes_for: [JAILBREAK]` 역할이 유지된다. 로컬에서 확인했다.

확률은 kanana 와 같은 **단일 토큰 로짓**으로 얻는다 — Gen 은 판정을 텍스트로 내므로
접두를 강제로 넣고 다음 토큰 분포를 읽는다. 후보 첫 토큰이 겹치지 않는 것을 구성 시점에
확인하고, 겹치면 실패한다.

### ⚠️ 반드시 함께 재야 하는 것 — JAILBREAK 동의율

§6.2.2 가 **Gen-0.6B 의 폴란드 FPR 17.17%** 를 명시했다(Bielik Guard 실사용 측정).
과탐이 심한 교사가 2표 중 1표를 맡으면 **거의 항상 동의해서 사실상 Gemma 단독 판정**이
된다 — 설계가 세 번째 교사를 넣어 막으려던 바로 그 상태다.

```
python -m bench.teacher_agreement \
  --teacher kanana=<...> --teacher gemma4=<...> --teacher qwen3guard_gen=<...> \
  --primary gemma4 --secondary qwen3guard_gen
```

**동의율 ≥ 0.95 면 파이프라인이 멈춘다.** 같은 하네스가 **생성기 라벨과 교사 라벨의 일치도**도
낸다 — 자작 공격 코퍼스를 외부 모델로 검증할 유일한 기회다.

#### 6b 가 멈췄을 때의 판정 — 교사 산출물이 생기기 전(2026-10-26)에 정해 둔다

높은 동의율만으로는 "따라 하기" 인지 "진짜 일치" 인지 갈리지 않는다. 하네스가 함께 내는
`jailbreak_on_primary_negative`(Gemma 가 JAILBREAK 이 **아니라** 한 행에서 Qwen 이 JAILBREAK
이라 한 비율)로 가른다.

| 동의율 | Gemma-음성 위 Qwen JAILBREAK 비율 | 판정 | 행동 |
|---|---|---|---|
| < 0.95 | — | 통과 | 그대로 진행 |
| ≥ 0.95 | **< 0.05** | 진짜 일치 — Qwen 이 혼자서는 거의 JAILBREAK 이라 하지 않는다 | `--accept-rubber-stamp` 로 진행하고 두 수치를 모델 카드에 적는다 |
| ≥ 0.95 | **≥ 0.05** | 따라 하기 — 무엇에나 JAILBREAK 이라 하는 교사다 | **Qwen3Guard-Gen-4B 로 교체** 후 Qwen 단계만 다시 |

**교체 절차(Gen-0.6B → Gen-4B).** 같은 계열·같은 택소노미라 `Qwen3GuardGenBackend` 가 그대로
맞고, 원격 코드가 없고, Apache-2.0 이며 `train/models.yaml` 에 revision 이 이미 고정돼 있다
(`l2_qwen3guard_gen_4b`). 한국어 F1 0.6B 77.1 → 4B 85.7(§6.4.3), bf16 약 9GB 라 24GB 에 들어간다.
이름(`qwen3guard_gen`)은 그대로 두고 모델만 바꾼다 — 경로·합의 규칙이 그대로 이어진다.

1. `train/models.yaml` 의 `teacher_qwen3guard_gen` 과 `train/teachers.yaml` 의 `qwen3guard_gen` 에서
   `model_id` → `Qwen/Qwen3Guard-Gen-4B`, `revision` → `6ec42827da0c1ff11e7a49dc269d2e810d27e108`,
   `parameters` → `4411424256`
2. `python train/validate_models.py` → `python train/download_assets.py --execute`
3. `data/corpus/teacher-qwen3guard_gen.jsonl` 을 코퍼스 밖으로 옮긴다(대조용) — 그대로 두면
   `run_teacher.py` 가 `existing output model/revision drift` 로 거부한다
4. `python train/runpod_pipeline.py --device cuda --train` 재실행 — kanana·Gemma 는 이어 돌기라
   건너뛰고 Qwen 만 다시 돈다. 4B 로도 따라 하기면 사람이 판단한다(JAILBREAK 를 Gemma 단독
   판정으로 받을지, 그 클래스를 이번 학습에서 뺄지)

#### 실제로 일어난 일 — 표에 없던 반대쪽 실패(RTX 3090, 2026-10-27)

Gen-0.6B 는 따라 하기가 아니라 **거의 알아보지 못했다.** 6b 는 통과했다 — 게이트는 동의율이
**높을** 때만 멈추기 때문이다.

| 0.6B 6b 지표 | 값 |
|---|---|
| Gemma 가 JAILBREAK 이라 한 행 | 1,596 |
| 그중 Qwen 도 JAILBREAK (`agreement_on_jailbreak`) | **0.036** |
| Gemma-음성 위 Qwen JAILBREAK (`jailbreak_on_primary_negative`) | 0.004 |
| 생성기 JAILBREAK 159행 위 Qwen 재현율 | **0.145** (136행을 Safe) |

JAILBREAK 는 Gemma·Qwen 2표가 있어야 받으므로 합의 결과가 **학습 53행 · 검증 5행**이었다.
백엔드 결함이 아님은 확인했다 — 생성기 JAILBREAK 행에서 Safe 확신도 평균이 0.996 이었다.
0.6B 가 한국어 탈옥을 탈옥으로 보지 않는 것이다(§6.2.2 가 경고한 다국어 약점과 같은 방향).

**판단: 미리 정한 교체 대상(Gen-4B)으로 바꾼다.** 표의 행동은 따라 하기에 대해 정해 둔 것이라
이 경우를 덮지 않는다 — 그래서 **교사 산출물을 본 뒤, 학습·평가 전**에 내린 결정임을 적어 둔다.
L1 결과는 아무것도 보지 않았다. 검증 5행으로는 JAILBREAK head 를 고를 수도 잴 수도 없고,
같은 계열의 상위 모델로 바꾸는 것이 합의 규칙(2표)을 느슨하게 하는 것보다 덜 자의적이다.
0.6B 산출물과 6b 보고서는 pod 의 `/workspace/backup-qwen0p6b/` 에 남겨 두고 결과와 함께 보고한다
(회수본: HF dataset `cycloevan/garim-guard` 의 `runpod/2026-10-27/backup-qwen0p6b/`).

## 학습 전 확인할 것 — 해소됨

이전 판은 여기에 `pii_per_attack` 결정을 남겨 뒀다. PolyGuard 어댑터를 고치면서
불균형이 88:1 → 5.9:1 이 되어 **결정할 것이 없어졌다.** 아래는 기록으로 남긴다.



```
PII 108,588 : 공격 1,238 = 88:1   (§6 추정은 54:1)
pii_per_attack=4 → 공격 1행을 에폭당 22번, 5에폭이면 110번
```

T4 는 평가 전용이라 이 비율을 바꾸지 않는다. 그대로 갈지, `pii_per_attack` 을 올릴지,
공격 학습 풀을 키울지는 **9번 preflight 통과 후 10번 전에** 정한다. 바꾸면 재봉인이다.
