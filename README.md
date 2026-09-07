# 가림(Garim)

가림은 앱·챗봇의 프롬프트가 외부 LLM API로 나가기 전에 한국 개인정보, 시크릿,
기밀 표식과 프롬프트 공격을 로컬에서 검사하는 라이브러리입니다. 현재 저장소에는
외부 모델 없이 동작하는 P0(L0) 코어와 L1 다중 헤드 모델의 학습·내보내기 코드가
구현되어 있습니다. 실제 학습 가중치는 포함하지 않습니다. 전체 기획과 평가 계획은
[설계 문서](docs/DESIGN.md)를 기준으로 하며, 코드 완료 범위와 미실행 작업은
[구현 현황](docs/IMPLEMENTATION_STATUS.md)에, 학습 전까지 실측한 수치는
[측정 결과](docs/RESULTS.md)에 기록합니다.

## 현재 구현 범위

- 손실 없는 세그먼트 분리와 prose/code/structured/log 타입 판정
- 7단계 가역 정규화, `norm_nospace`, base64/hex 1회 디코드, 원문 스팬 역매핑
- 한국 식별번호·공통 PII·시크릿·기밀 표식·고정밀 공격 문구 L0 규칙
- `allow / mask / warn / block` 정책과 YAML 검증
- 세션/태스크 볼트, 동일값 토큰 유지, 관용적 토큰 복원과 한국어 조사 교정
- L0 전체 세그먼트 검사, L1 토큰 청킹과 공정한 bounded budget
- L1 타임아웃 취소·회로 차단, 청크 중복 스팬 병합
- L1 공유 인코더 + PII/ATK/보조 2개 공동 모델·마스킹 손실·ONNX QInt8 내보내기
- HF revision/라이선스·KDPII SHA-256 fail-closed 데이터 게이트
- KDPII 반복 식별자 연결 성분 분할과 공격 train/test 지문 누수 검사
- 클래스별 교사 자격표와 불일치 검토 큐 판정
- 라이브러리 API와 `garim check`, `garim scan` CLI

L1 ONNX 가중치와 L2 로컬 LLM은 아직 저장소에 포함하지 않습니다. L1 백엔드를
주입하지 않은 상태는 정상적인 P0 프로파일이며 `degraded`로 표시되지 않습니다.
현재 `thorough` 모드는 L2 미구현을 숨기지 않고 `warn`, `degraded=true`를 반환합니다.

학습 코드는 `garim/training/`, 실행 스크립트와 고정 manifest는 `train/`에 있습니다.
기본 동작은 로컬 전용이며 `JointL1Model.from_pretrained()`도 명시적으로 변경하지 않는
한 원격 다운로드를 허용하지 않습니다.

2026-09-07 기준으로 학습 준비 코드, 고정 모델·데이터 자산과 `implementation`/`assets`
preflight는 완료되었습니다. split 코퍼스도 로컬에 생성했습니다. deepset 번역, 교사 3종
전체 합의, 최종 평가 gold, 실제 CUDA 학습과 ONNX 성능 측정은 아직 완료되지 않았으므로
`training` preflight는 해당 입력을 모두 전달하고 두 입력 manifest를 봉인하기 전까지
실패하는 것이 정상입니다.

## L1 학습 준비

학습 준비 명령은 기본적으로 다운로드나 학습을 시작하지 않습니다. 네트워크 다운로드와
optimizer 실행에는 각각 `--execute`가 필요합니다.

```bash
# 재현 가능한 Python 3.11 학습 환경
uv sync --extra train --extra dev
python train/preflight.py --stage implementation

# revision·예상 저장 위치 확인만 수행
python train/download_assets.py

# 검토된 revision의 모델과 데이터만 다운로드
python train/download_assets.py --execute

# 이미 snapshot_download로 받은 고정 자산은 네트워크 없이 원격 tree/LFS 해시를 검증
python train/download_assets.py --record-existing

# 공개 값 사전 + 공개 코퍼스 + 로컬 합성 코퍼스 준비
# 체크섬 통과 난수는 실제 식별자와 우연히 일치할 수 있으므로 data/ 아래에만 둔다.
python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers
```

`deepset` 영어 원문은 train/test를 먼저 봉인한 뒤 번역한다. 번역 JSONL은 각 행에
`id`, `text`, `method`를 넣으며 ID 누락·추가 시 실패한다.

```bash
python train/apply_translations.py \
  --queue data/corpus/deepset_train_queue-train.jsonl \
  --translations data/local/deepset-train-ko.jsonl \
  --output data/corpus/deepset-ko-train.jsonl

python train/apply_translations.py \
  --queue data/corpus/deepset_eval_queue-test.jsonl \
  --translations data/local/deepset-test-ko.jsonl \
  --output data/corpus/deepset-ko-test.jsonl
```

공개 공격 풀은 외부 test 지문을 제거한 뒤 세 교사로 각각 라벨링한다. 교사 실행도
`--execute`를 빼면 행 수와 pinned revision만 점검합니다.

```bash
python train/merge_corpora.py \
  --input data/corpus/polyguard_train-train.jsonl \
  --input data/corpus/deepset-ko-train.jsonl \
  --blocked-eval data/corpus/polyguard_eval-test.jsonl \
  --blocked-eval data/corpus/deepset-ko-test.jsonl \
  --task attack --split train \
  --output data/corpus/attack-teacher-pool-train.jsonl

python train/run_teacher.py --teacher kanana \
  --model-dir models/pinned/teacher_kanana \
  --input data/corpus/attack-teacher-pool-train.jsonl \
  --output data/corpus/teacher-kanana.jsonl --execute
python train/run_teacher.py --teacher gemma4 \
  --model-dir models/pinned/teacher_gemma4 \
  --input data/corpus/attack-teacher-pool-train.jsonl \
  --output data/corpus/teacher-gemma4.jsonl --execute
python train/run_teacher.py --teacher qwen3guard_gen \
  --model-dir models/pinned/teacher_qwen3guard_gen \
  --input data/corpus/attack-teacher-pool-train.jsonl \
  --output data/corpus/teacher-qwen.jsonl --execute

python -m bench.teacher_agreement \
  --teacher kanana=data/corpus/teacher-kanana.jsonl \
  --teacher gemma4=data/corpus/teacher-gemma4.jsonl \
  --teacher qwen3guard_gen=data/corpus/teacher-qwen.jsonl

python train/resolve_teacher_labels.py \
  --input data/corpus/attack-teacher-pool-train.jsonl \
  --teacher kanana=data/corpus/teacher-kanana.jsonl \
  --teacher gemma4=data/corpus/teacher-gemma4.jsonl \
  --teacher qwen3guard_gen=data/corpus/teacher-qwen.jsonl \
  --output-dir data/corpus/resolved
```

교사 불일치와 누락은 `attack-teacher-review.jsonl`로 빠지며 자동으로 `NONE`에
흡수되지 않습니다. [라벨링 가이드](docs/LABELING_GUIDE.md)를 먼저 고정하고 교사 예측을
보지 않은 채 라벨합니다. 라벨러에게는 원천 라벨·교사 예측·metadata를 제거한
블라인드 큐만 전달합니다.

```bash
python train/create_labeling_queue.py \
  --input data/corpus/resolved/attack-teacher-review.jsonl \
  --purpose teacher-review \
  --output data/local/teacher-review-blind.jsonl

python train/apply_review_labels.py \
  --queue data/corpus/resolved/attack-teacher-review.jsonl \
  --labels data/local/teacher-review-labels.jsonl \
  --output-dir data/corpus/resolved
```

accepted 및 human-reviewed train/validation만 공동 학습에 넣습니다.

번역·교사·사람 판정이 모두 끝나면 실제 학습에 사용할 파일 집합을 먼저 봉인합니다.
이 명령은 코퍼스 의미 검증과 전체 pinned asset 검증을 다시 수행하고 각 입력 파일,
`training.yaml`, 모델·소스 manifest와 asset manifest의 SHA-256을 기록합니다.

```bash
python train/finalize_training_inputs.py \
  --pii-train data/corpus/pii-synthetic-train.jsonl \
  --pii-train data/corpus/kdpii-train.jsonl \
  --pii-train data/corpus/klue_train-train.jsonl \
  --pii-validation data/corpus/pii-synthetic-validation.jsonl \
  --pii-validation data/corpus/kdpii-validation.jsonl \
  --attack-train data/corpus/attack-synthetic-train.jsonl \
  --attack-train data/corpus/resolved/attack-teacher-accepted-train.jsonl \
  --attack-train data/corpus/resolved/attack-human-reviewed-train.jsonl \
  --attack-validation data/corpus/attack-synthetic-validation.jsonl \
  --attack-validation data/corpus/resolved/attack-teacher-accepted-validation.jsonl \
  --attack-validation data/corpus/resolved/attack-human-reviewed-validation.jsonl
```

아래 명령은 tokenizer·스팬 정렬, A.X 단일 forward와 모든 누수·미해결 라벨 게이트까지
확인합니다. optimizer는 만들지 않습니다. 마지막에 `--execute`를 추가해야 3개 seed의
실제 학습이 시작됩니다.

```bash
python train/train_l1.py \
  --encoder-dir models/pinned/l1_ax_encoder \
  --pii-train data/corpus/pii-synthetic-train.jsonl \
  --pii-train data/corpus/kdpii-train.jsonl \
  --pii-train data/corpus/klue_train-train.jsonl \
  --pii-validation data/corpus/pii-synthetic-validation.jsonl \
  --pii-validation data/corpus/kdpii-validation.jsonl \
  --attack-train data/corpus/attack-synthetic-train.jsonl \
  --attack-train data/corpus/resolved/attack-teacher-accepted-train.jsonl \
  --attack-train data/corpus/resolved/attack-human-reviewed-train.jsonl \
  --attack-validation data/corpus/attack-synthetic-validation.jsonl \
  --attack-validation data/corpus/resolved/attack-teacher-accepted-validation.jsonl \
  --attack-validation data/corpus/resolved/attack-human-reviewed-validation.jsonl
```

`train_l1.py`와 `preflight.py --stage training`은 기본적으로
`data/training-input-manifest.json`을 다시 검증합니다. 봉인 후 입력·설정·asset manifest 중
하나라도 바뀌면 dry-run과 실제 학습 모두 시작하지 않습니다.

KLUE validation, PolyGuardPrompts, deepset test, KDPII test, 합성 test와
bc-pii-ko augmented-negative는 학습·early stopping 입력이 아니라 최종 평가 전용입니다.
PolyGuardPrompts와 번역된 deepset test는 학습 전에 블라인드 수동 골드를 고정합니다.

```bash
python train/create_labeling_queue.py \
  --input data/corpus/polyguard_eval-test.jsonl \
  --input data/corpus/deepset-ko-test.jsonl \
  --purpose final-evaluation \
  --output data/local/final-attack-eval-blind.jsonl

python train/apply_eval_labels.py \
  --input data/corpus/polyguard_eval-test.jsonl \
  --input data/corpus/deepset-ko-test.jsonl \
  --labels data/local/final-attack-eval-labels.jsonl \
  --output data/corpus/attack-manual-gold-test.jsonl

# 사람 검수를 생략하는 운영은 택소노미가 1:1인 deepset 이진 gold만 자동 적용한다.
# PolyGuard harm 라벨은 프로젝트 4-way 라벨로 추측 변환하지 않는다.
python train/apply_external_eval_labels.py \
  --input data/corpus/deepset-ko-test.jsonl \
  --output data/corpus/attack-external-gold-test.jsonl

# optimizer를 만들기 전에 최종 test와 라벨링 근거를 해시로 봉인한다.
python train/finalize_evaluation_inputs.py \
  --input data/corpus/pii-synthetic-test.jsonl \
  --input data/corpus/attack-synthetic-test.jsonl \
  --input data/corpus/attack-manual-gold-test.jsonl \
  --input data/corpus/kdpii-test.jsonl \
  --input data/corpus/klue_eval-test.jsonl \
  --input data/corpus/bc_pii_augmented_negative-test.jsonl
```

`evaluate_l1.py`는 `split=test`뿐 아니라 번역·교사·수동 라벨 미해결 flag와 공격
gold label 누락도 거부하고, `evaluation-input-manifest.json`의 파일 SHA-256과 봉인된
학습 입력과의 정규화 지문 교집합을 다시 검사합니다. 이 평가 봉인은 실제 학습보다 먼저
완료해야 하며 `train_l1.py`와 `preflight.py --stage training`도 이를 요구합니다.
학습 후에는 `train/export_l1.py`로 FP32와 QInt8을 내보낸 다음, 봉인된 test 파일만
`train/evaluate_l1.py`에 전달하고 `train/benchmark_l1.py`로 128/256/512토큰의
p50/p95·파일 크기·RSS를 측정합니다. 결과 템플릿은
[`train/MODEL_CARD_TEMPLATE.md`](train/MODEL_CARD_TEMPLATE.md)에 있습니다.
`evaluate_l1.py --baseline-model-file garim_l1.fp32.onnx`를 함께 주면 QInt8에서
FP32 대비 떨어진 공격 macro 지표와 PII 태그별 character recall을 별도로 기록합니다.

## 설치와 사용

Python 3.11 환경에서 다음과 같이 설치합니다.

```bash
python -m pip install -e '.[dev]'
garim check '연락처는 user@example.test 입니다'
printf '검사할 프롬프트' | garim check --json
garim scan ./prompts
```

라이브러리에서는 입력 역할을 보존할 수 있습니다.

```python
from garim import Inspector, Segment

inspector = Inspector("config/policy.yaml")
result = inspector.check([
    Segment("당신은 상담 도우미입니다.", origin="system"),
    Segment("문의 내용을 확인해 주세요.", origin="user"),
])

if result.masked_segments is not None:
    outbound_messages = result.masked_segments
```

`CheckResult.public_dict()`는 볼트 원문을 직렬화하지 않습니다. 외부 API의 응답은
같은 `result.vault`의 `restore()`로 앱에 돌아오는 경로에서만 복원합니다.

## 개발

```bash
python -m pytest
ruff check garim tests tools train
python train/validate_sources.py
python train/validate_models.py
```

`[dev]`만 설치하면 torch가 필요한 학습·내보내기 테스트는 skip됩니다. 학습 코드를
고칠 때는 CI의 `train-stack` 잡과 같은 환경에서 함께 돌려야 합니다.

```bash
uv sync --extra train --extra dev
python -m pytest tests -k "training or l1"
```

`tests/test_training_onnx_roundtrip.py`는 고정 자산 없이 작은 인코더와 토크나이저만으로
torch 모델 → ONNX → QInt8 → `OnnxL1Backend` 전 구간을 검증합니다. 계약 검증이며
정확도·지연 측정이 아닙니다.

코드는 Apache-2.0입니다. KLUE-NER 같은 외부 데이터로 만든 가중치를 공개할 때는
모델 카드에 데이터 출처·라이선스를 전수 기록하고 가중치 라이선스를 별도로 검토합니다.
