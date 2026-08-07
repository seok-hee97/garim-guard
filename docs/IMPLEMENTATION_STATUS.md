# 가림 구현 현황

> 기준일: 2026-08-07  
> 범위: 실제 모델 다운로드와 GPU 학습을 시작하기 전까지의 구현 상태

이 문서는 [DESIGN.md](DESIGN.md)의 기획을 현재 저장소 코드에 대응시킨 실행 현황이다.
여기서 **구현 완료**는 코드·설정·검증 경로가 준비됐다는 뜻이며, 모델 가중치 다운로드,
교사 추론, 사람 검수, 학생 학습과 성능 측정까지 끝났다는 뜻은 아니다.

## 상태 요약

| 영역 | 상태 | 현재 산출물 | 아직 실행하지 않은 것 |
|---|---|---|---|
| P0/L0 런타임 | **구현 완료** | 세그먼트, 가역 정규화, 규칙 탐지, 병합, 정책, 볼트, API/CLI | 실 운영 트래픽 성능 측정 |
| L1 런타임 계약 | **구현 완료** | 512/64 토큰 청킹, bounded budget, 타임아웃·회로 차단, ONNX 백엔드 | 학습된 ONNX 가중치 연결 |
| 모델·데이터 고정 | **구현 완료** | 모델 6개, 데이터 소스 12개 revision/라이선스 레지스트리, exact 파일 manifest, `uv.lock` | 전체 자산 다운로드 및 파일별 manifest 생성 |
| 코퍼스 준비 | **구현 완료** | 공통 JSONL 계약, 원천별 어댑터, 분할·중복·누수 차단 | 원천 데이터를 내려받아 실제 코퍼스 파일 생성 |
| 합성 PII/공격 데이터 | **구현 완료** | 5만 건 기본 분포 생성기, 템플릿·값 split 격리, span 검증 | 생성 결과를 `data/corpus/`에 영속화 |
| 번역 | **인터페이스 완료** | split 봉인, ID 완전성 검사, 원문 해시 보존 | deepset train/test 한국어 번역 작성·적용 |
| 교사·수동 라벨링 | **파이프라인 완료** | 교사 3종 로컬 추론, 클래스별 합의, metadata-free 블라인드 큐, train/test exact-coverage 적용 | 교사 모델 실행과 사람 블라인드 검수 |
| L1 공동 학습 | **구현 완료** | SHA-256 입력 봉인, A.X 공유 백본+4개 헤드, 4:1 샘플링, soft/aux loss, 3 seed, 이중 early stopping | optimizer를 사용하는 실제 GPU 학습 |
| 내보내기·평가 | **구현 완료** | FP32/QInt8 ONNX, 봉인 test 평가, 양자화 저하, latency/RSS 하네스 | 체크포인트 export와 수치 측정 |
| L2 | **비활성** | 후보만 manifest에 고정, `thorough`의 degraded 응답 | 모델 통합·검증·활성화 |

현재 단계는 **학습 준비 코드 완료**다. 저장소에는 L1 학습 가중치, 전체 원천 데이터,
교사 예측 결과, 사람 검수 결과와 측정 성능값이 포함되어 있지 않다.

## 구현된 구성요소

### 1. 재현 가능한 환경과 자산 게이트

- Python 3.11과 학습 의존성을 [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock)에 고정했다.
- [models.yaml](../train/models.yaml)은 L1 A.X 백본과 교사 3종을 exact revision으로 고정한다.
- [sources.yaml](../train/sources.yaml)은 12개 학습·평가·값 사전 소스의 용도와 라이선스를 고정한다.
- [download_assets.py](../train/download_assets.py)는 기본적으로 계획만 출력하며 `--execute`가
  있어야 다운로드한다. 다운로드 후 모든 파일의 SHA-256과 크기를
  `data/asset-manifest.json`에 기록한다.
- manifest에 없던 파일이 자산 디렉터리에 추가되어도 drift로 처리한다. 로컬 코드를
  로드하는 모델 디렉터리에 미검토 파일이 섞이는 경로를 허용하지 않는다.
- [preflight.py](../train/preflight.py)는 `implementation`, `assets`, `training` 세 단계로
  구현 파일, 고정 자산, 로컬 인코더, 코퍼스 입력을 각각 검사한다. `training` 단계는
  단순 파일 수뿐 아니라 task/split, 미해결 번역·교사·수동 라벨과 전체 누수를 검사한다.

### 2. 코퍼스 계약과 데이터 누수 방지

- [data.py](../garim/training/data.py)에 PII/공격 공통 JSONL 스키마와 span·label·split 검증을 구현했다.
- [adapters.py](../garim/training/adapters.py)에 KDPII, KLUE-NER, PolyGuard,
  deepset, bc-pii-ko 변환기를 구현했다.
- KDPII는 같은 식별자가 반복되는 대화 연결 성분을 먼저 묶어 split 간 값 누수를 막는다.
- KLUE-NER은 `PS` 중 한글 2~4자 성명만 PERSON으로 사용하고 `LC`, `OG`는 변환하지 않는다.
- bc-pii-ko는 문맥 학습 데이터가 아니라 값 사전으로만 사용하며
  `augmented-negative` 720행은 test FPR 전용으로 분리한다.
- PolyGuard와 deepset은 train/test 원문 및 정규화 지문 교집합을 차단한다.
- [apply_translations.py](../train/apply_translations.py)는 번역 전 split을 유지하며
  ID 누락이나 추가가 하나라도 있으면 실패한다.

### 3. 합성 데이터와 교사 라벨링

- [synthetic.py](../garim/training/synthetic.py)는 기본 50,000건 PII 코퍼스와
  공격 600건·정상 1,200건을 결정적으로 생성한다.
- PII 14개 라벨은 기본 설정에서 각각 2,000개 이상의 span을 가지며, prose/code/
  structured/log/mixed/unknown 분포와 동일 문자열 양성·음성 문맥 쌍을 검사한다.
- 생성 값과 템플릿은 split 단위로 격리하며 모든 문자 span을 재검증한다.
- [teacher_inference.py](../garim/training/teacher_inference.py)는 로컬 모델 디렉터리만 읽는다.
  Kanana 3-class soft target, Gemma 4 hard label, Qwen jailbreak posterior를 생성한다.
- [teacher_io.py](../garim/training/teacher_io.py)는 클래스별 최소 2표 합의를 요구하고,
  누락·불일치를 `NONE`으로 흡수하지 않고 사람 검토 큐로 보낸다.
- [create_labeling_queue.py](../train/create_labeling_queue.py)는 원천 라벨·교사 예측·metadata를
  제거한 `id`, `text` 전용 파일을 만들고, [apply_eval_labels.py](../train/apply_eval_labels.py)는
  최종 공격 test의 수동 gold를 정확한 ID 집합으로만 적용한다.
- 사람 판정 기준과 결과 JSONL 계약은 [LABELING_GUIDE.md](LABELING_GUIDE.md)에 고정했다.

### 4. L1 학습·내보내기·평가

- [training.yaml](../train/training.yaml)에 seed `13/37/73`, epoch 5, batch 32,
  max length 512, stride 64, PII:공격 4:1, learning rate 3e-5를 고정했다.
- [finalize_training_inputs.py](../train/finalize_training_inputs.py)는 번역·교사·사람 판정이
  끝난 네 입력 그룹과 설정·모델·소스·asset manifest를 SHA-256으로 봉인하고 `git_commit`을
  함께 기록한다. preflight와 trainer가 이 파일을 재검증하므로 봉인 뒤 변경된 입력으로
  학습할 수 없다.
- 봉인은 저장소 기준 상대경로로 파일을 지목한다(schema 2). 절대경로를 기록하던 schema 1은
  준비와 학습을 다른 머신에서 하거나 제3자가 재현할 때 내용이 같아도 위치만으로 실패했고,
  공개 산출물의 봉인으로는 성립하지 않았다. 동일성 판정은 여전히 SHA-256이 한다.
- [dataset.py](../garim/training/dataset.py)는 런타임 정규화와 같은 경로로 raw span을
  normalized token window에 투영하며, 엔티티가 어떤 window에도 완전히 들어가지 않으면 실패한다.
- [train_l1.py](../train/train_l1.py)는 PII token classification과 공격 4-way 분류,
  Romanized/Honorific 보조 헤드를 한 A.X 백본에서 공동 학습한다.
- PII 행에서는 공격 loss를, 공격 행에서는 PII loss를 마스킹한다. 교사 soft target과
  O-label 가중치도 학습 설정에서 제어한다.
- A.X가 받지 않는 tokenizer의 `token_type_ids`는 encoder forward signature 기준으로
  제거한다. 실제 optimizer 실행은 `--execute`를 요구하며, 옵션이 없으면 tokenizer·span
  정렬, 코퍼스 게이트와 로컬 A.X 4-head 단일 forward까지 검사한다.
- 검증 지표는 손실과 다른 텐서를 읽는다. 공격 행은 `pii_labels`로 PII 손실을 마스킹하되
  `pii_eval_labels`에서 실토큰을 `O`로 두어, 공격 텍스트 위의 PII 오탐이 character
  precision에 잡히게 한다. 두 텐서를 하나로 쓰면 §7.1.1이 헤드라인으로 내건 FPR을
  early stopping이 보지 못한다.
- [export_l1.py](../train/export_l1.py)는 FP32와 signed per-channel QInt8 ONNX를 만들고,
  [evaluate_l1.py](../train/evaluate_l1.py)는 봉인 test에서 PII character 지표와 공격
  macro 지표를 계산한다. 미라벨 공격 행이나 미해결 flag가 있으면 평가 전에 실패한다.
- [benchmark_l1.py](../train/benchmark_l1.py)는 128/256/512 토큰별 p50/p95, 파일 크기와 RSS를 기록한다.

## 2026-08-07 검증 기록

다음 검증은 전체 모델·데이터 다운로드나 학습 없이 수행했다.

- 전체 pytest 테스트 스위트 통과 — 의존성 없는 환경과 `[train]` 환경 양쪽에서 실행
- `ruff check garim tests tools train` 통과
- `uv lock --check` 통과 — 93개 패키지 해석
- `train/validate_sources.py` 통과 — 등록 소스 12개
- `train/validate_models.py` 통과 — 고정 모델 6개, 클래스 범위 교사 3개
- `train/preflight.py --stage implementation` 통과
- 모든 `train/` CLI import 및 `--help` 검사 통과
- 기본 50,000건 합성 코퍼스를 메모리에서 생성해 라벨 수, 입력 형태 분포,
  span 정합성과 split 누수 없음을 확인
- 설계 프로브 10종 전부 실행 통과
- `git diff --check` 통과

### torch·ONNX 계층을 실제로 실행해서 잡은 것

`dev` extra가 pytest·ruff뿐이라 torch 테스트가 **로컬에서도 CI에서도 통째로 skip**되고
있었다. `JointL1Model`·손실·엔진·ONNX 내보내기·`OnnxL1Backend`이 한 줄도 실행되지 않은
채 "구현 완료"로 기록돼 있었고, 첫 실행이 GPU 박스일 뻔했다. CI에 CPU torch 잡을 만들고
`tests/test_training_onnx_roundtrip.py`로 작은 인코더·토크나이저만으로 전 구간을 돌린
결과, **내보내기 경로가 세 군데에서 깨져 있었다**.

| 결함 | 증상 | 조치 |
|---|---|---|
| `onnxscript` 미선언 | `torch.onnx.export`가 dynamo 익스포터를 기본값으로 쓰는데 그 모듈을 import한다. `ModuleNotFoundError`로 파이프라인 마지막 단계가 전부 실패 | `train` extra에 추가, `uv.lock` 재생성 |
| `Wrapper`가 train 모드 | `model.eval()`은 내부 모델에만 걸렸고 `torch.onnx.export`는 넘겨받은 모듈의 플래그를 본다. **dropout이 켜진 채로 추론 그래프에 traced** | `wrapper.eval()` |
| `dynamic_axes` + dynamo | 호환 shim이 `batch`와 `sequence`를 한 제약으로 합쳐, `quantize_dynamic`의 shape inference가 `(24) vs (4)`로 실패 | `dynamic_shapes` + `torch.export.Dim.DYNAMIC`, opset 18 |
| 양자화 전처리 누락 | dynamo 그래프는 initializer에도 `value_info`를 남기고, 양자화기가 `Gemm` B를 전치한 뒤 그 stale 정보와 충돌 | `quant_pre_process` 선행 |

세 번째와 네 번째는 같은 증상으로 나타났고 둘 다 고쳐야 통과한다. 셋 다 실행하기
전에는 어떤 정적 검사로도 드러나지 않았다.

성능 수치는 아직 없다. 합성 데이터 검증 결과는 구현 회귀 증거이며 실제 분포의
재현율·FPR 증거로 사용하지 않는다. 위 왕복 테스트도 **계약 검증이지 품질 측정이
아니다** — 무작위 가중치의 작은 인코더이므로 정확도·지연 근거로 쓰지 않는다.

## 학습을 시작하기 전에 남은 실행

아래는 미구현 작업이 아니라 외부 자산·연산·사람 판정이 필요한 실행 단계다.

1. `uv sync --extra train --extra dev`로 학습 환경을 동기화한다.
2. `python train/download_assets.py --execute`로 고정 자산을 다운로드하고 체크섬을 기록한다.
3. `python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers`로 split 코퍼스를 생성한다.
4. deepset train/test를 별도로 번역하고 ID 완전성 게이트를 통과시킨다.
5. 공격 학습 풀에 교사 3종을 실행하고 합의 라벨과 사람 검토 큐를 만든다.
6. metadata-free 블라인드 큐로 교사 불일치와 최종 공격 test를 수동 검수한다.
7. 수동 검수 결과를 train/validation과 봉인 test에 exact ID coverage로 적용한다.
8. `finalize_training_inputs.py`로 네 코퍼스 그룹과 모든 provenance 파일을 봉인한다.
9. `preflight.py --stage training`으로 로컬 인코더, 봉인 manifest와 학습 패키지를 확인한다.
10. `train_l1.py`의 단일 forward dry-run 후 승인된 실행에서만 `--execute`로 학습한다.

구체 명령과 파일 연결 순서는 [README의 L1 학습 준비](../README.md#l1-학습-준비)를 따른다.
`assets`와 `training` preflight는 위 자산이 없는 현재 상태에서는 실패하는 것이 정상이다.

## 학습 후에만 채울 항목

- seed별 checkpoint와 선택 근거
- FP32/QInt8 PII character recall/F1과 공격 macro recall/F1/FPR
- QInt8의 FP32 대비 태그별·공격 클래스별 저하
- 128/256/512 토큰 p50/p95와 기준 머신 RSS
- 데이터 파일 SHA-256, 실행 commit, 하드웨어와 학습 시간
- KLUE-NER 사용 사실과 가중치 공개 라이선스 검토 결과

측정 전에는 [MODEL_CARD_TEMPLATE.md](../train/MODEL_CARD_TEMPLATE.md)의 `TODO` 값을
예상치로 채우지 않는다.
