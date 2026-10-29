# 가림 구현 현황

> 기준일: 2026-10-29 — R1 배포 · R2 판정(불채택) · GitHub · HF 공개 · PyPI `garim-guard` 0.1.5 (이전 판 2026-10-28)
> 범위: 고정 자산 준비와 로컬 교사 추론을 포함한 현재 구현·실행 상태

이 문서는 [DESIGN.md](DESIGN.md)의 기획을 현재 저장소 코드에 대응시킨 실행 현황이다.
여기서 **구현 완료**는 코드·설정·검증 경로가 준비됐다는 뜻이며, 모델 가중치 다운로드,
교사 추론, 사람 검수, 학생 학습과 성능 측정까지 끝났다는 뜻은 아니다.

## 상태 요약

| 영역 | 상태 | 현재 산출물 | 아직 실행하지 않은 것 |
|---|---|---|---|
| P0/L0 런타임 | **구현 완료** | 세그먼트, 가역 정규화, 규칙 탐지, 병합, 정책, 볼트, API/CLI | 실 운영 트래픽 성능 측정 |
| L1 런타임 계약 | **구현 완료 · 학습 가중치로 확인** | 512/64 토큰 청킹, bounded budget, 타임아웃·회로 차단, ONNX 백엔드, `--l1-model-dir`, 프록시 스레드 간 공유. `train/artifacts/l1-onnx` 로 CLI 전 구간 통과(PERSON 마스킹 · 인젝션 classifier) | — |
| 외부 모델 연결 | **구현 완료(0.1.3)** | `--l1-backend` · `--l2-backend`(`모듈:함수` · `파일.py:함수`), `garim.backends.load_backend`, bench 의 `--l1-backend`, 예시 셋(`examples/backends/`) · 계약 문서([INTEGRATION.md](INTEGRATION.md)) | 실제 외부 NER · 분류기로 bench 측정 |
| 문서 첨부 검사 | **구현 완료(0.1.4 · 재검토 수정 0.1.5)** | 프록시 · `garim scan` 이 DOCX · XLSX · PPTX · HWPX · ODF · PDF(`[docs]`) 의 글을 꺼내 검사, 정책 `attachments.unmaskable` · `unreadable` · `uninspected`, PDF 는 별도 프로세스 20초 제한 — `garim/attachments.py` | 이미지 안 글자(OCR) · 오디오 · 레거시 HWP/DOC |
| 모델·데이터 고정 | **구현·로컬 검증 완료** | 활성 자산 18개, revision/라이선스 레지스트리, 원격 tree/LFS 검증, exact 파일 manifest, `uv.lock` | 새 머신에서 동일 자산 재현 |
| 코퍼스 준비 | **구현·로컬 생성 완료** | 공통 JSONL 계약, 원천별 어댑터, 분할·중복·누수 차단, `data/corpus/` 산출물 | — |
| 합성 PII/공격 데이터 | **구현·로컬 생성 완료** | 5만 건 기본 분포 생성기, 템플릿·값 split 격리, span 검증 | 외부 분포에서 품질 검증 |
| 번역 | **완료** | deepset 662행 — Gemini 3.8 Flash 50 + Claude 612(한도 초과로 전환, 행별 `method`), `train/translations/`, `deepset-ko-{train,test}` 적용 | — |
| 교사·수동 라벨링 | **완료(RTX 3090, 2026-10-27)** | 교사 3종 × 19,157행(기권 0) — kanana · Gemma 4 E2B · **Qwen3Guard-Gen-4B**(0.6B 가 한국어 탈옥을 못 봐 교체). 클래스별 2표 합의 14,020 수용 / 5,137 폐기(감사 기록) | R2 실행 완료 — 새 합성 2,466행만 교사 실행(17,734행 R1 재사용), C3 로 생성기 정상 채택률 0.998 |
| L1 공동 학습 | **완료 — 3 seed × 5 epoch** | bf16, micro-batch 16×2. 검증 hmean 0.896 / **0.922** / 0.918 → seed 37 | R2(3 seed, seed 37) 학습·판정 완료 — **불채택**(O1 · O4 미달, [RESULTS_L1_R2.md](RESULTS_L1_R2.md)). SYSTEM_LEAK 보강은 R3 후보(일정 없음) |
| 내보내기·평가 | **완료** | QInt8 152MB · 양자화 저하 −0.0002 · 봉인 test 17,146행 · 외부 KoreanGuardrail 0.342/0.007 · TAB · 제품 경로 PII(`bench/l1_product_path.py`) — [RESULTS_L1.md](RESULTS_L1.md). 0.1.3 규칙으로 M1 재측정 0.349/0.007 · KDPII 0.988/0.985 — [RESULTS §10](RESULTS.md) | 노트북 128토큰 지연 재측정(pod CPU 값만 있음, M1 2스레드 행당 평균 48ms 만 있음). 배포 INT8 은 `reduce_range` 판으로 교체 — VNNI 없는 x86 의 U8S8 포화 수정 |
| L2 | **구현 · Gemma 4 E2B 판정기 연결** | `garim/detect/l2.py` — 루프백 llama-server, 교사와 같은 정책 프롬프트, 3-스트림 분리, 파싱 실패 warn+degraded. 고정 revision → Q8_0 → Q4_K_M GGUF | interactive 에스컬레이션(§6.4.6 W1 게이트), kanana L2-fast |
| RunPod 실행 | **완료(2026-10-27)** | R1: RTX 3090 · torch cu126(드라이버 CUDA 12.8), 결함 셋 수정(Gemma 설명 출력 · KDPII 앞 공백 · Qwen 교체). R2: 해시 게이트 · 교사 재사용 · 학습 · 판정, 평가 단계 결함 둘 수정(CPU 할당량 스레드 · INT8 포화) — `docs/RUNPOD.md`. 산출물 회수·sha256 대조, HF 보관(model · dataset 공개, 합성 주민번호 파일은 비공개 dataset) | — |
| 합성 코퍼스 축 | **표기·은닉 축 신설** | §5.3.2 표기 5분할(`notation.py`) · §2.2 은닉 9종(`hiding.py`) · 라벨 누수 제거(§6.3.0) · 델타 feature 8→7칸 | 없음 |
| §7.3 베이스라인 | **하네스 완료 / 7종 실측** | 축 (b) 정규화 ON/OFF 대조, PolyGuard·kanana·**Gemma 4 L2**, detect-secrets·CredSweeper(축 d), Presidio 3팔(H1) | Gemini — 유료 tier 키 필요(§7.4) |
| 평가 하네스 | **H1·H2·S4·8f·축(a)·지연·13b 완료** | `bench/` — attack_eval · leak_eval · ko_guardrail · tab · cred_data · type_eval · real_fpr · latency · presidio_baseline · secret_baselines · mask_quality | 코퍼스 수집 확대, Gemini 팔, 학습 ONNX 실측 |
| 로컬 프록시(§8.1-17) | **구현 완료** | OpenAI 호환 `garim proxy`, 차단 미전달, 돌아오는 길 복원, **스트리밍은 모아서 복원한 뒤 SSE**(실모델 확인, `--refuse-streaming` 선택), 엄격 프로필(`garim/policy/profiles/strict.yaml`(`--policy strict`)), **`tool_calls` 인자 검사·복원**, **그 밖의 요청 필드 검사**(`tools` 설명 · `prediction` · `user` · 메시지 `name`, 2026-10-28), 상류 오류 본문 전달, `--upstream-timeout`, **실모델 전 구간 통과**(`bench/proxy_e2e.py`, 로컬 Gemma 상류) | Gemini 상류 실호출(유료 키) |
| 외부통신 0(§8.1-15b) | **검증 완료** | `tools/no_external_calls.py` — 소켓 봉인, CI 게이트 | — |

**2026-10-28 공개 후 강화.** 코드만 준 독립 검토를 다섯 번 돌려 고쳤다 — 프록시가 검사하지 않던 요청 필드 전부,
짝 없는 surrogate · 보이지 않는 문자 · 분리된 받침으로 L0 를 비껴가던 것, 이메일 목록 · 문장 끝 마침표, 정규식의
제곱 시간 6곳과 탐지가 많을 때의 병합(연락처 1,200줄 12.6초 → 0.9초), 긴 숫자의 검사 중단, 볼트 토큰 충돌 · 조사 교정,
`garim scan` 의 큰 파일 · 심볼릭 링크. L0 규칙이 바뀌었으므로 아래 수치는 그 전 측정이다 — L0 만 다시 잰 전후는
[RESULTS §8](RESULTS.md)(KDPII L0 재현율 0.374 → 0.528, 음성 오탐 · 공격 · 시크릿 변화 없음).

현재 단계는 **L1 두 차례 학습·판정 완료**다(2026-10-27). 배포 모델은 R1(seed 37) — 봉인 test ·
외부 벤치 수치는 [RESULTS_L1.md](RESULTS_L1.md). R1 결함을 고치려던 **R2** 는 사전 등록
([PREREG_L1_R2.md](PREREG_L1_R2.md))대로 학습·판정했고, 오탐은 거의 없앴지만 외부 공격 탐지(O1)와
KLUE 이름 재현율(O4)이 통과선에 못 미쳐 **채택하지 않았다**([RESULTS_L1_R2.md](RESULTS_L1_R2.md)). 그 측정 중
INT8 이 VNNI 없는 x86 에서 깨지는 결함을 찾아 배포 INT8 을 `reduce_range` 판으로 바꿨다. 모델 · 데이터는
HF `cycloevan/garim-guard`(model · dataset — L1 가중치 CC-BY-SA-4.0)에 있다. 2026-10-28 이 상태로 공개하고
마무리했다. 다음 학습(R3)은 일정에 없다 — 한다면 새 사전 등록부터 하고, 후보는
[RESULTS_L1_R2 §3](RESULTS_L1_R2.md) 에 적어 두었다. 재지 못한 것은 노트북 지연과 Gemini 측정(유료 키)이다.

## 구현된 구성요소

### 1. 재현 가능한 환경과 자산 게이트

- 의존성을 [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock)에 고정했다. 런타임은 Python 3.11 ~ 3.14(CI 가
  판마다 돈다), 학습 재현은 3.11 이다(DESIGN §8.6.2).
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

## 2026-08-07 추가 — 기밀 표식 수정과 L2 축별 평가

### 기밀 표식(§2.5)이 설계 실측을 재현하지 못하고 있었다

배포 코드 `markers.py` 를 설계 프로브의 자기 케이스 10건으로 돌린 결과
**재현율 60% / 오탐 40%** 였다. §2.5 가 주장한 값은 100% / 0% 다. 원인 셋:

1. **사전 25개 중 22개가 없었다.** `대외비`·`사내한`·`CONFIDENTIAL` 셋뿐이고
   `기밀`·`영업비밀`·`극비`·`취급주의`·`비밀`·`秘`·`RESTRICTED`·`TLP:RED` 가 전부 빠졌다.
2. `보안등급` 이 `[1-4]급` 숫자형만 받아 `보안등급: 기밀` 을 놓쳤다.
3. 위치 가중치가 **상대 오프셋 하나**로 근사돼 있었다. §2.5 가 실측한 것은
   `첫3줄 +2 / 단독·괄호 +3 / 등급표기 +3 / 조사결합 −3, 임계 3` 의 4-특징 방식이다.

`docs/probes/marker_probe.py` 가 통과하고 있던 것은 그 스크립트가 알고리즘을 자기 안에 다시
구현해 검사했기 때문이다 — 배포 코드를 한 번도 부르지 않았다. 사전과 가중치를 설계대로
채웠고 임계를 `rules.marker_score_threshold` 정책으로 뺐다(§2.5 가 재조정을 요구한다).
배포 코드를 실제로 부르는 회귀 테스트를 `tests/test_markers.py` 로 붙였고, 프로브는
garim 을 import 할 수 있으면 참조 구현과의 일치까지 확인한다.

수정 후 설계 케이스 **100% / 0%**, 외부 정상문(KoreanGuardrail benign 2,244행)에서
**오탐 0건**이다.

### L2 는 축 (b) 만 목적 전용 모델이 있다

축별 모델·평가셋·실측 현황과 수치는 [L2_EVALUATION.md](L2_EVALUATION.md)에 있다.
요점 셋만 여기 남긴다.

- 공개 평가셋을 6개 등록했다 — KoreanGuardrail·korean-guardrail-test·XL-SafetyBench
  (축 b), TAB(축 a), CredData(축 d), nym-pii(축 a, **품질 문제로 disabled**).
  축 (c) 기밀 표식은 영어까지 뒤져도 공개 자산이 존재하지 않아 자체 제작이 불가피하다.
- PolyGuard 는 L2-fast 후보로 검토했다가 **택소노미 불일치로 기각**하고 베이스라인으로
  내렸다. 라이선스·언어·GGUF 는 다 맞았지만 재는 축이 harm 카테고리다.
- ⚠️→✅ **`kanana-safeguard-prompt-2.1b` 가 `transformers>=5.5` 에서 로드되지 않았다.**
  `head_dim` 을 명시한 decoupled 구성인데 `validate_architecture` 가 그걸 보지 않는다.
  `teacher_kanana` 로 L1 증류 교사에 고정된 같은 모델이라 GPU 박스의 교사 추론이 같은
  지점에서 실패했을 것이다. [`training/compat.py`](../garim/training/compat.py) 로 해소.

### 은닉 축 둘과 사람 없는 운영

- **A-KO-02 초성체**를 초성 서명 방식으로 구현했다. L0 사전 커버리지 안에서 난독화
  내성이 `chosung` ~0% → **89%**, 전체 81% → **86%** 다. 기존 `_CHOSEONG` 은 연속
  3패턴만 봐서 `ㅇㅈ ㅈㅅ는 ㅈ부` 같은 실제 형태를 전부 놓쳤다.
- **A-KO-07 된소리화**를 신설했다(`detensified` 스트림). §2.2 에 축 자체가 없었다.
  `tensify` 19% → **47%**.
- **사람 검수 없는 운영**을 코드에 반영했다 — `resolve_teacher_labels.py --drop-review`
  (`NONE` 흡수 금지는 유지, 폐기는 감사에 기록)와
  [`external_labels.py`](../garim/training/external_labels.py)(택소노미 1:1 인 외부
  라벨만 수용, harm 라벨은 거부). **T3 수동 골드셋은 미수행**으로 확정된다.
- **15c `train/license_check.py`** 를 만들었다. 기존 `validate_*.py` 는 정적 검사이고,
  HF 런타임 조회는 `download_assets.py --execute` 안에서만 돌았다.

수치와 근거는 [L2_EVALUATION.md](L2_EVALUATION.md) 에, 학습 전 실측 전체는
[RESULTS.md](RESULTS.md) 에 한계와 함께 모아 뒀다.

## 2026-10-26 갱신 — 다이어그램 경로를 실제 입력으로 돌려 본 결과

앱 → 검사(L0·L1·판정) → Gemini/OpenAI → 복원 → 앱 경로를 실제 입력으로 돌려 점검했다.
L0·판정·마스킹·복원은 동작했고, 아래를 고쳤다. 테스트 503 → **557개 통과**, ruff·외부통신
봉인·커밋 PII 검사 통과.

### 1. 프록시가 함수 호출 인자를 원문 그대로 상류로 보냈다

`segments_from_messages` 는 `content` 만 읽는다. 함수 호출 대화는 매 요청에 이전 턴의
assistant `tool_calls` 를 함께 보내는데, 그 인자가 검사 없이 나갔다. 같은 요청에서 `tool`
메시지의 이메일은 `<EMAIL_1>` 로 가려지고 바로 앞 `tool_calls` 의 같은 이메일은 원문이었다.
응답 쪽은 `content` 만 복원해서 앱의 도구가 `{"to": "<PHONE_1>"}` 을 받았다.

- 요청: `tool_calls[].function.arguments`·`function_call.arguments` 를 `structured` 세그먼트로
  검사한다. `\uXXXX` 이스케이프를 풀어 검사하고, 마스킹 후 JSON 이 깨지면 보내지 않는다.
- 응답: 인자의 문자열 리프만 복원해 다시 직렬화한다(따옴표가 든 값이 인자를 깨지 않게).
- 같은 값은 `content` 와 인자에서 같은 토큰으로 묶인다(볼트 공유).
- 상류 오류 본문을 `detail` 로 넘긴다(429·503 구분), `--upstream-timeout` 추가.

### 2. L1 은 학습이 끝나도 꽂을 자리가 없었다

`l1_backend=` 는 라이브러리 API 에만 있었다. CLI·프록시에 `--l1-model-dir` 를 넣었다.
프록시는 요청마다 Inspector(볼트 격리)를 만들고 백엔드는 공유하는데, 공유를 견디려면 둘이
필요했다.

- **토크나이저 직렬화.** 런타임이 fast tokenizer 를 두 설정(청킹은 무절단, 추론은 절단)으로
  번갈아 부른다. 락을 빼고 8스레드 400회를 돌리면 **`RuntimeError: Already borrowed`** 로
  죽는 것을 재현했다(청킹 경로라 try 밖 — 프록시 500). 락으로 해소, 회귀 테스트 고정.
- **스레드별 취소.** RunOptions 슬롯이 하나라 요청 A 의 타임아웃이 요청 B 의 추론을 끊을 수
  있었다. 워커 스레드 id 로 추적한다.

### 3. 조사 교정이 실제로 가리는 값에 한 번도 안 걸렸다

교정이 한글로 끝나는 값에만 걸렸는데 L0 가 가리는 값은 전부 숫자로 끝난다
(`010-1234-5678를`). 끝자리 숫자의 한자어 읽기로 교정한다. 토큰 뒤 서술격 `이에요` 는
건드리지 않는다(`김철수가에요` 방지). DESIGN 부록 A.4 에 반영.

### 4. L2 판정기 — Gemma 4 E2B 를 온디바이스로 연결했다

`garim/detect/l2.py`. 루프백 OpenAI 호환 서버(llama-server·Ollama)에만 붙고 비루프백은
생성 시점에 거부한다(`tools/no_external_calls.py` 에도 경계 검사 추가). 정책 프롬프트는
교사와 한 출처다. 설계 결정은 DESIGN §6.4.7.

- 자산: `google/gemma-4-E2B-it@3e22461` → llama.cpp `620d2225c` 변환기 → Q8_0
  (sha256 `354c1c55…`, 4.95GB) → Q4_K_M 재양자화(sha256 `b44e5586…`, 3.4GB).
  `models/derived/` — Git 밖.
- 축 (b) KoreanGuardrail 600: 단독 **0.633 / 0.017**, 스트림 분리 **0.810 / 0.057**,
  행당 1.17초 / 2.46초(M1). 상세는 [L2_EVALUATION.md](L2_EVALUATION.md).
- 실제 CLI 왕복: `garim --mode thorough --l2-endpoint … check` 가 유출·탈옥·된소리 변형을
  잡고 정상 요약 요청은 통과, degraded 없음.

### 5. L1 데이터 — 번역을 끝내고 교사 입력을 확정했다

- deepset 662행 번역: Gemini 3.8 Flash 50행 + Claude 612행. Gemini 키가 하루 한도에 걸려
  나머지를 옮겼고, 그 사실이 행마다 `method` 에 남는다. 번역본은 `train/translations/` 에
  저장소와 함께 둔다(LLM 번역은 재생성이 비트 단위로 같지 않다. 고지는 NOTICE).
- 교사 풀은 처음 19,025행(기존 18,511 + deepset 514)이었고, 아래 8번의 차단 목록이 들어가
  **19,010행**(평가 지문 18 · PII 학습 문장 20 제거), 교사 입력 **19,195행**이 됐다. 이후 교사 입력 길이 상한(2,048토큰)으로 38행을 더 빼 **19,157행**이다.
  deepset 외부 이진 gold 116행 생성.
- **교사 실행은 RunPod 으로 넘긴다.** 정족수 2표에서 kanana 는 NONE/INJECTION/SYSTEM_LEAK,
  Qwen 은 JAILBREAK 만 투표하므로 Gemma 없이 로컬 두 교사만으로는 어떤 행도 합의되지 않는다.
  Gemma 교사는 bf16 transformers 라 GPU 가 필요하다(GGUF 로 돌리면 교사 provenance 가 바뀐다).

### 6. 학습 봉인은 낡았다 — 교사 라벨 뒤에 다시 봉인한다

`data/training-input-manifest.json`(c587e82)은 해시가 맞지만 PolyGuard 언어 필터 수정
**이전** 것이라 공격 학습이 합성 1,238행뿐이다. `train_l1.py` 의 증류 게이트(교사 라벨
0행이면 거부)가 그대로 학습하는 것을 막는다. RUNPOD.md 6~8b 단계에서 다시 봉인한다.

### 7. 외부 API — 오늘은 둘 다 쓸 수 없었다

- **Gemini 키는 Free Tier 로 확정됐다.** 오류 본문:
  `generate_content_free_tier_requests, limit: 20`(모델당 하루 20회). 마스킹 품질(204회)과
  프록시 실호출(3.8-flash 는 503·240초 타임아웃)을 끝내지 못했다. 유료 키가 필요하다.
- **OpenAI 키는 크레딧이 0** 이다(`credit_balance_exhausted`).
- ⚠️ 한도를 확인하기 전에 합성 프롬프트 일부와 공개 deepset 10행이 Free Tier 로 나갔다.
  §7.4(유료 tier 만)를 어겼다. 실제 개인정보는 없었다.

### 7b. 프록시 전 구간을 실제 모델로 — 통과

`bench/proxy_e2e.py` 가 실제 `garim proxy` 서버를 루프백 임의 포트에 띄우고 합성 PII 를
OpenAI 호환 상류로 보낸다. 모델에게 받은 줄을 `<>`→`[]` 로 바꿔 한 번, 그대로 한 번 옮겨 적게
해서 **상류가 무엇을 받았는지 상류의 입으로** 확인한다(프록시는 `<…>` 만 복원하므로).

로컬 Gemma 4 llama-server 를 상류로 둔 결과 — 첫 줄 `연락처 [PHONE_1], 메일 [EMAIL_1]`
(상류는 토큰만 받았다), 둘째 줄은 원문으로 복원, `X-Garim-Verdict: mask`, degraded 없음.
다이어그램의 "마스킹된 채로 → LLM API → 복원" 구간을 실모델로 처음 끝까지 돌렸다. 같은
명령에 상류만 바꾸면 Gemini 로 잰다(아래 유료 키 목록).

### 8. RunPod 리허설 — 교사 실행 뒤 단계에서 결함 다섯을 찾았다

교사 라벨링 이후 단계(6b~10)는 실제 코퍼스로 끝까지 돈 적이 없었다. GPU 교사를 다 돌린
뒤에 배선 결함을 찾으면 그 시간이 버려진다. 교사 대역(형식·ID·투표 구조만 같은 지어낸
라벨)으로 실제 CLI 를 돌리는 리허설을 만들어 돌렸고, 다섯이 나왔다. 상세 표는
[RUNPOD.md](RUNPOD.md#리허설이-찾은-것--교사-실행-뒤-단계의-결함-다섯-2026-10-26).

1. **6b 일치도 하네스** — 기본 gold 경로가 13e09b8 이전 이름이었고, 교사 산출물이 gold 와
   정확히 같은 ID 이기를 요구해 풀이 커진 뒤 `extra=17,787` 로 멈추게 돼 있었다.
2. **7단계 분할** — `group or template` 한 키로 버킷을 정해 합성 template 300개 전부가 갈릴
   수 있었다. union-find 성분 단위로 바꿨다(교사 합의분·사람 검수분 공통).
3. **PolyGuard group** — `metadata.source`(하위 데이터셋 이름)를 행 ID 로 읽어 17,273행이 group
   둘로 뭉쳤다. 검증 분할에 PolyGuard 가 0행이었고, 해시가 반대였다면 공격 데이터 84% 가
   검증으로 빠졌다. 행 단위 group 으로 고치고 기존 코퍼스에 같은 규칙을 적용했다(ID·표본
   불변, load→write 왕복이 바이트 동일함을 먼저 확인).
4. **8단계 교차 태스크 중복** — `네, 맞습니다.` 류가 KDPII 와 PolyGuard 에 같이 있었다.
   `merge_corpora --blocked-other-task` 로 공격 풀에서 PII 학습 문장을 뺀다.
5. **8b 평가 지문 중복** — PII 병합에 평가 차단 목록이 없었다(KDPII test 일반 발화, 상류
   KLUE 의 라벨까지 같은 train·validation 문장). 학습 쪽에서 뺀다 — 평가셋과 봉인 규칙은
   그대로다. PII 학습 108,588 → 108,499, 검증 7,734 → 7,702.

리허설 통과 후 `train/runpod_pipeline.py` 를 단계의 유일한 출처로 삼았다 — README 의 수동
명령은 PII 파일을 평가 차단 없이 넣고, 합성 공격을 생성기 라벨과 교사 라벨로 **두 번**
넣는 조합이라 그대로 따랐다면 봉인에서 멈췄을 것이다. 정리했다.

⚠️ 리허설은 결정적 코퍼스 단계(PII 병합·교사 풀)를 RunPod 과 똑같이 다시 만든다. 그래서
`data/training-input-manifest.json`(c587e82) 은 이제 검증에 실패한다 — 원래 다시 봉인할
대상이었다.

### 9. RunPod 전 정확성 검토 — "새 pod 에서 저장소만으로 도는가"

로컬에서 돈 것이 아니라 **clone 한 저장소만으로 새 머신에서 도는가**를 기준으로 다시 봤다.

| 항목 | 결과 |
|---|---|
| 작업 브랜치가 원격에 있는가 | ❌ → **푸시함**(2026-10-26). 그전까지 원격에는 `main`(구 프로젝트)뿐이었다. 저장소는 **비공개**라 pod 에서는 읽기 전용 토큰으로 clone 한다(RUNPOD.md) |
| RUNPOD.md clone 명령 | ❌ → 고침. `git clone <repo> && cd safeagent-kr` 는 기본 브랜치(구 프로젝트)를 받고 디렉터리 이름도 틀렸다 |
| T4·T4′ 생성 단계 | ❌ → 고침. 커밋된 생성 스크립트가 없었다(9a8fec8 에서 한 번 손으로 씀). 새 pod 에선 3단계가 파일을 못 찾는다. 오케스트레이터 2b 단계로 넣었고 재생성본이 로컬본과 바이트 동일 |
| 코퍼스 재현 | ✅ `prepare_corpora` 를 원자산에서 다시 돌려 16개 전부 바이트 동일 — PolyGuard `group_id` 제자리 수정이 고친 어댑터 출력과 같다는 증명 |
| 실제 A.X 로 학습 루프 | ✅ CPU 미니 학습(64+32행, 3 seed × 1 epoch, 128토큰) — 학습·체크포인트·seed 보고서 정상, 최대 메모리 3.2GB |
| **실제 그래프 양자화** | ❌ → 고침. `quant_pre_process` 의 symbolic shape inference 가 ModernBERT 의 동적 `Range` 에서 죽었다. 대역 인코더엔 `Range` 가 없어 테스트가 통과했다 — **학습을 다 마친 뒤 11단계에서 드러날 자리**. 익스포터의 stale `value_info` 를 비우고 symbolic 패스를 끈다. 재현 테스트 추가(고치기 전 코드에서 실패 확인) |
| 내보내기 → 평가 → 벤치 → 런타임 | ✅ FP32 595MB · INT8 152MB(설계 예측 ~149MB), FP32 대비 공격 확률 차 ≤ 0.03, 봉인 입력 평가·FP32 기준 대비 저하 보고, `garim --l1-model-dir` 에서 L1 동작(degraded 없음) |
| CUDA | ⚠️ torch 2.13 휠이 CUDA 13 런타임 — 드라이버가 CUDA 13 을 지원하는 pod 필요. 오케스트레이터 0단계에서 먼저 확인하고 멈춘다 |
| seed 선택 | ⚠️ 자동 아님 — RUNPOD.md 11단계에 기준과 명령, pod 종료 전 회수 목록을 적었다 |
| **교사 로짓 메모리 (24GB)** | ❌ → 고침. kanana·Qwen 이 배치 전체 위치의 로짓(배치 × 길이 × 어휘)을 만든 뒤 한 칸만 골랐다. 교사 입력 최장 행이 약 4만 토큰이라 kanana(어휘 128k) 16행 배치의 로짓만 약 164GB — 어떤 GPU 에서도 그 배치에서 죽는다. 마지막 실토큰 위치에만 `lm_head` 를 건다. 실제 Qwen 에서 옛 경로와 max\|Δlogit\| 0, argmax 동일 |
| **교사 입력 길이** | ❌ → 고침. kanana 문맥(8,192)을 넘는 행이 4개, 2,048토큰 초과 38행(0.2%, 전부 PolyGuardMix 문서류). 2,048 초과는 교사 풀에서 빼고 `attack-teacher-overlength.jsonl` 에 남긴다 |
| **학습 메모리 (24GB)** | ⚠️ → 손잡이 추가. fp32 · 32행 × 512토큰이 24GB 에 들어간다는 근거가 없었다. 논리 배치 32 는 그대로, `micro_batch_size: 16`(기울기 누적)과 CUDA bf16 autocast(`precision: bf16`, master fp32). 청크 학습이 전체 배치와 같은 스텝임을 SGD 로 확인하는 테스트(가중을 깨뜨리면 실패함을 확인) |
| **0b GPU 메모리 프로브** | 신설. 교사보다 먼저 학습 최악 배치(32행 전부 512토큰, 실제 데이터셋·콜레이터 모양)를 GPU 에서 두 스텝 돌려 최대 할당량을 잰다. VRAM 85% 초과면 멈춘다 |

**첫 INT8 지연 실측(실제 A.X 그래프) — 판정용 아님.** 미니 학습 체크포인트를 내보낸 INT8(그래프·크기는
본 학습과 같다)을 이 Mac 에서 쟀다: 128토큰 p50 233ms / 256토큰 587ms / 512토큰 942ms, 로드 후 RSS
653MB. ⚠️ 다른 실험이 CPU 를 쓰는 중(load ≈ 12)에 `nice 15` · 3스레드로 잰 값이라 §7.1.3 예산(L1
160ms)과 비교할 수 없다. 그래도 §4.4 의 "INT8 50~150ms" 가정과 0.15GB 메모리 예산이 크게 낙관적일
수 있다는 신호다. **지연은 학습과 무관하므로** 기계가 한가할 때
`python train/benchmark_l1.py --model-dir train/artifacts/smoke/onnx --output …` 로 다시 재면
§8.5-1(W1 게이트)이 RunPod 없이 판정된다.

### 10. 결정 사항 반영 (2026-10-26)

- **스트리밍 — buffered SSE.** 채팅 앱 대부분이 `stream=True` 인데 프록시가 거부해서 앱이 깨졌다.
  상류에는 스트리밍 없이 묻고(토큰이 청크 경계에 걸리지 않게) 전문을 복원한 뒤 OpenAI
  `chat.completion.chunk` SSE 로 준다 — 본문·함수 인자·`include_usage` 사용량 청크·`[DONE]`.
  차단은 SSE 가 아니라 400 JSON(OpenAI SDK 는 스트림을 읽기 전에 상태로 예외를 낸다). 로컬
  Gemma 를 상류로 둔 `bench/proxy_e2e.py --stream` 에서 상류는 토큰만 받았고 클라이언트가 이어
  붙인 문자열은 원문으로 복원됐다. 대가는 첫 글자 지연(응답 전체만큼).
- **엄격 프로필 `garim/policy/profiles/strict.yaml`(`--policy strict`).** 기본은 공격 `warn` 유지(L1 오탐률 측정 전).
  게이트웨이로 막아야 하는 배치용 — 인젝션(직접·간접)·시스템 프롬프트 유출·기밀 표식 `block`,
  탈옥·일반 고엔트로피는 `warn`. L1 평가에서 공격 오탐률이 §7.1.1 목표(≤ 0.02) 안이면 그때
  기본값 상향을 다시 정한다.
- **KoreanGuardrail 라이선스** — CC-BY-4.0 확인, DESIGN §8.5-14 닫음.
- **구 프로젝트 잔여물 삭제** — `app/` `eval/` `src/` `scripts/`(캐시뿐), `report/`(빈 폴더),
  `results/`(구 로그), `models/gpu_trained/`(682MB, 가림 미사용·재배포 금지 모델). 전부 Git 밖이었다.
- **지연·메모리 예산** — 기계가 한가할 때 재측정 후 결정(§9). RunPod 과 무관.

## 학습을 시작하기 전에 남은 실행

아래는 외부 연산·번역 또는 독립 판정이 필요한 실행 단계다. 2026-10-26 로컬 상태를
함께 표시한다. 4번부터는 RunPod 에서 한다([RUNPOD.md](RUNPOD.md)).

1. ✅ `.venv` 학습 환경과 전체 고정 자산 18개를 준비하고 exact manifest를 검증했다.
2. ✅ 공개 원천과 합성 split 코퍼스를 생성하고 누수·span 계약을 검사했다.
3. ✅ deepset train/test를 번역하고 ID 완전성 게이트를 통과시켰다. 교사 입력 19,157행을 확정했고,
   `python train/runpod_pipeline.py --rehearse` 로 3~10단계 배선을 교사 대역으로 끝까지 돌렸다.
4. ✅ 교사 3종 × 19,157행(RTX 3090, 2026-10-27). JAILBREAK 교사를 Gen-0.6B → Gen-4B 로 교체했다(RUNPOD.md 6b).
5. ✅ 6b 통과 — JAILBREAK 동의율 0.091(rubber-stamp 아님), 생성기 JAILBREAK 재현율 0.453.
6. ✅ `--drop-review` — 5,137행 폐기(감사 기록). ⚠️ 폐기분에 생성기 NONE 의 하드 네거티브 379행이 들었다(RESULTS_L1 §5-②).
7. ✅ 최종 공격 test는 deepset의 1:1 외부 이진 gold를 만들었다(`attack-external-gold-test.jsonl`, 116행).
8. ✅ 학습 130,221 레코드 · 평가 17,146 레코드 봉인.
9. ✅ preflight · dry-run 통과(pod 리허설 포함).
10. ✅ 3 seed 학습 → seed 37 → FP32/QInt8 export · 봉인 test 평가 · pod CPU 지연/RSS. 결과는 [RESULTS_L1.md](RESULTS_L1.md).

4~10번은 `python train/runpod_pipeline.py --device cuda --train` 한 명령이다. RunPod 을 켜기
전에 `--rehearse` 로 같은 경로를 교사 대역으로 먼저 돌린다(2026-10-26 통과). 단계표는
[RUNPOD.md](RUNPOD.md).

### 외부 API — 유료 키가 생기면 (DESIGN §7.4)

현 Gemini 키는 Free Tier(모델당 하루 20회)라 아래를 끝낼 수 없다. 전부 합성·공개 데이터다.

| 측정 | 명령 | 호출 수 |
|---|---|---|
| 마스킹 응답 품질 Gemini 팔(§8.1-13b) | `python -m bench.mask_quality --provider gemini --output artifacts/mask_quality_gemini.json` | 204 |
| 프록시 전 구간 — Gemini 상류 | `python -m bench.proxy_e2e --upstream https://generativelanguage.googleapis.com/v1beta/openai --model gemini-3.8-flash --api-key-env GEMINI_API_KEY` | 1 |
| 축 (b) Gemini 베이스라인(§7.3) | `python -m bench.run_axis_b --benchmark <KoreanGuardrail>/benchmark.jsonl --sample 600 --judge gemini --judge gemini-all-streams` | 약 2,000 |

축 (b) 의 Gemini judge 는 온디바이스 Gemma 4 L2·교사와 **같은 정책 프롬프트**를 쓴다 —
같은 질문을 클라우드와 온디바이스에 던진 비교가 된다.

## 학습 후에만 채울 항목

2026-10-27 채웠다 — [RESULTS_L1.md](RESULTS_L1.md) §1·§3·§4·§8, 모델 카드는
[MODEL_CARD_L1.md](MODEL_CARD_L1.md). 남은 것은 **기준 머신(노트북) 지연·RSS** 다. 가중치 라이선스는 CC-BY-SA-4.0 으로 정했다
(2026-10-28, [MODEL_CARD_L1.md](MODEL_CARD_L1.md)).

- seed별 checkpoint와 선택 근거
- FP32/QInt8 PII character recall/F1과 공격 macro recall/F1/FPR
- QInt8의 FP32 대비 태그별·공격 클래스별 저하
- 128/256/512 토큰 p50/p95와 기준 머신 RSS
- 데이터 파일 SHA-256, 실행 commit, 하드웨어와 학습 시간
- KLUE-NER 사용 사실과 가중치 공개 라이선스 검토 결과

측정 전에는 [MODEL_CARD_TEMPLATE.md](../train/MODEL_CARD_TEMPLATE.md)의 `TODO` 값을
예상치로 채우지 않는다.
