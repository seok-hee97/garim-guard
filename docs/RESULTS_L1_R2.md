# 가림 측정 결과 — L1 2차 학습(R2) 판정

> 기준일: 2026-10-27 · RunPod RTX 3090(교사·학습) / AMD EPYC 7452 x86(판정 측정)
> 사전 등록: [PREREG_L1_R2.md](PREREG_L1_R2.md) — 바꿀 것 · 판정 기준 · 분석 방법을 학습 전에 고정했다.
> 짝 문서: [RESULTS_L1.md](RESULTS_L1.md) — R1(첫 학습) 결과와 R2 를 하게 된 결함.

**결론 — R2 는 채택하지 않는다.** 주 지표 넷 중 O1 · O4 가 통과선에 못 미쳤다. 사전 등록 §4 대로 배포
모델은 R1(seed 37)을 유지한다. 단, 측정 중 R1 의 INT8 이 일부 x86 CPU 에서 깨지는 결함을 찾아
(§4-①) 배포 INT8 을 `reduce_range` 로 다시 양자화한 판으로 바꿨다 — 가중치는 R1 그대로다.

## 0. 판정 — 사전 등록 §4 표 그대로

기준선은 같은 pod · 같은 세션 · 같은 양자화로 다시 잰 R1 이다. 판정은 `bench/r2_verdict.py`(통과선을
결과 전에 코드로 고정, 커밋 `1e8932e`)가 냈다. 비율 옆 괄호는 family 단위 cluster bootstrap 95% 구간이다.

| # | 지표 | 셋 | R1 | **R2** | 통과선 | 판정 |
|---|---|---|---:|---:|---|---|
| O1 | 공격 탐지율 / 과방어율 | KoreanGuardrail 5,555 | 0.342 / 0.007 | **0.316** / 0.010 | 탐지 ≥ 0.322 · 과방어 ≤ 0.010 | ❌ |
| O2 | NONE→공격 FPR | `t4-r2-test` 정상 700 · 27 family | 0.439 (0.321–0.547) | **0.000** (0–0) | ≤ 0.05 | ✅ |
| O3 | PII 음성 오탐 행 | `pii-negative-r2-test` 1,589 · 20 family | 0.515 (0.332–0.708) | **0.000** | ≤ 2.0% | ✅ |
| O3 | 같은 지표 | bc-pii-ko 보강 음성 720 · 12 문형 | 0.088 (63행) | **0.000** (0행) | ≤ 2.0% | ✅ |
| O4 | KDPII P / R | KDPII test 5,438 | 0.979 / 0.977 | 0.979 / 0.977 | ≥ 0.970 / 0.967 | ✅ |
| O4 | KLUE PERSON R | KLUE validation 5,000 | 0.931 | **0.906** | ≥ 0.922 | ❌ |
| O4 | 계좌 짝 양성 R | `pii-negative-r2-test` 250 · 3 family | 1.000 | 1.000 | ≥ 0.90 | ✅ |

O1 의 차이는 우연이 아니다 — 행별 판정 짝 McNemar 정확 검정(`bench/mcnemar_axis_b.py`):

| KoreanGuardrail | 행 | R1 만 맞힘 | R2 만 맞힘 | 정확 p |
|---|---:|---:|---:|---:|
| 공격 | 3,311 | 170 | 84 | 7.3 × 10⁻⁸ |
| 정상(과방어) | 2,244 | 0 | 6 | 0.031 |

## 1. 무엇을 돌렸나

| 단계 | 결과 |
|---|---|
| 해시 게이트 | pod 리허설 산출 15개(PII 병합 · 교사 입력 · 평가 9셋 · KDPII 3분할)가 로컬 리허설과 바이트 동일 |
| 교사 | 20,200행 중 17,734행은 R1 산출물 재사용(ID · 텍스트 sha256 둘 다 일치), 새 합성 2,466행만 3종 실행. 기권 0 |
| 6b | JAILBREAK 동의율 0.087(멈춤 0.95), Gemma-음성 위 Qwen JAILBREAK 0.014 |
| 7 · 7b (C3) | 수용 15,321 · 폐기 4,879. 생성기 정상 채택률 **0.998**(R1 은 954행 중 379행 폐기), 통째로 빠진 정상 family 0 |
| 생성기 공격 | 교사 합의가 생성기 라벨과 같을 때만 수용 — INJECTION 136 · JAILBREAK 84 · SYSTEM_LEAK 115 / 각 200 |
| 학습 입력 | 135,807 레코드. 공격 train NONE 12,908 · INJECTION 645 · JAILBREAK 97 · SYSTEM_LEAK 80 |
| seed 선택용 검증 | 공격 INJECTION 94 · JAILBREAK 42 · SYSTEM_LEAK 36(검토 전 1 · 4 → 템플릿 t2 고정) |
| 학습 | 3 seed × 5 epoch, 약 3시간 20분. 검증 hmean 0.525 / **0.762** / 0.547 → **seed 37(epoch 4)** — R1 과 같은 규칙 |
| 판정 측정 | x86 pod · R1 · R2 둘 다 `reduce_range` INT8 · onnxruntime intra-op 8스레드(§4) |

## 2. 보조 지표 — 보고만 한다

| # | 지표 | R1 | R2 | 비고 |
|---|---|---:|---:|---|
| S1 | KoreanGuardrail A2(유출) 탐지율 | 0.146 | 0.120 | "개선" 기준 0.20 에 못 미친다 — 오히려 내려갔다 |
| S2 | 새 T4 공격 macro recall | — | — | 재지 않는다(PREREG §8 첫 행 — 공격 홀드아웃 없음) |
| S3 | deepset INJECTION 재현율 / FPR | 0.183 / 0.000 | 0.217 / 0.000 | 116행 — 4행 차이 |
| S4 | `t4-r2-test` PII 제품 경로 오탐 행 | 0.011 | 0.000 | |
| S5 | KoreanGuardrail 600 표본(seed 13) | 0.330 / 0.003 | 0.313 / 0.007 | 교사와 같은 표본 |

KoreanGuardrail 기법별 탐지율:

| 기법 | R1 | R2 |
|---|---:|---:|
| clean · jamo_decompose | 0.542 | 0.492 |
| zwsp_inject | 0.537 | 0.492 |
| break_spacing | 0.239 | 0.223 |
| chosung | 0.181 | 0.178 |
| tensify | 0.113 | 0.110 |

## 3. 해석 — R2 는 전체가 "덜 잡는" 쪽으로 옮겨 갔다

- **사전 등록 §7 이 예상한 O1 위험과 실제 원인이 다르다.** 등록은 정상 은닉에 띄어쓰기 제거를 넣은
  것이 break_spacing 탐지를 깎을 위험을 적었다. 실제 하락은 그 셀(−0.016)보다 **clean · 자모 · 영폭
  셀(−0.05)** 에서 컸다 — 은닉이 아니라 모델의 공격 판단 전체가 보수적으로 옮겨 갔다.
- **PII 쪽도 같은 방향이다.** KLUE PERSON 은 정밀도 0.890 → 0.931, 재현율 0.931 → 0.906. C4 음성
  (숫자 문맥 4,000 · 낯선 문자 구성 3,000행)이 "애매하면 개체가 아니다" 를 가르쳤고, 그 대가가 이름 재현율이다.
- **O2 · O3 의 0.000 을 그대로 믿지 않는다.** 새 셋은 학습과 family 가 겹치지 않지만 같은 작성자가 쓴
  문장이다(PREREG §7). 외부 근거는 **bc-pii-ko 63 → 0 행** 하나이고, 이것은 실제 개선이다(R1 오탐 대부분이
  12자리 운송장번호였다).
- **seed 선택용 검증이 흔들린다.** 공격 검증 행이 처음 보는 템플릿 하나(클래스당 수십 행)라 epoch 마다
  macro recall 이 0.33 ~ 0.62 를 오갔고, seed 13 은 검증 FPR 이 가장 높은 epoch 1 이 골라졌다. R1 과 같은
  규칙이라 바꾸지 않았지만, 다음 학습의 선택 규칙은 이 검증을 그대로 쓰면 안 된다.

**다음 학습(R3) 후보** — 새 사전 등록이 먼저다. R2 의 test 셋은 이제 본 셋이다.
1. C4 음성의 비중을 줄이거나 PERSON 문맥 양성을 함께 늘려 KLUE 재현율을 지킨다.
2. 공격 쪽은 공개 데이터 재라벨로 SYSTEM_LEAK · 일반 공격 문장을 늘린다(R2 는 공격 문장을 새로 쓰지 않았다).
3. seed · epoch 선택용 검증을 외부 분포(예: KoreanGuardrail 과 겹치지 않는 공개 셋 일부)로 바꾼다.

## 4. 측정 중 찾은 결함 — 둘 다 배포 코드에 반영

### ① INT8 이 VNNI 없는 x86 에서 깨진다 — 제품 결함

판정 pod(EPYC 7452 — AVX2, VNNI 없음)에서 R1 을 기본 INT8 로 다시 재니 Mac 기준선과 크게 달랐다.

| R1 지표 | Mac(ARM) | 이 pod, 기본 INT8 |
|---|---:|---:|
| O2 정상 FPR | 0.436 | 0.271 |
| O3 bc 오탐 행 | 60 | 19 |
| O4 계좌 짝 R | 1.000 | 0.404 |

진단 표본 33행(`t4-r2-test` 정상 20 · 계좌 짝 양성 13)에서 판정(최상위 공격 라벨 · 개체 라벨)이 FP32 와
다른 행 수:

| 비교 | 다른 행 |
|---|---:|
| R1 pod INT8 vs FP32 | 22 |
| R2 pod INT8 vs pod FP32 | 17 (예: FP32 `KR_ACCOUNT` 1.0 → INT8 개체 없음) |
| R1 Mac INT8 vs Mac FP32 | 2 |
| **R2 pod INT8(`reduce_range`) vs pod FP32** | **0** |
| R1 Mac INT8(`reduce_range`) vs Mac FP32 | 3 |

동적 양자화는 활성 U8 · 가중치 S8 이고, onnxruntime 은 VNNI 없는 AVX2 에서 이 곱을 `vpmaddubsw` 로
계산해 int16 에서 포화한다. AMD Zen2 · Zen3, Ice Lake 이전 Intel 노트북이 해당한다.

- `quantize_joint_onnx` 에 `reduce_range=True`(7비트 가중치) — 커밋 `c2e7f2f`. 같은 FP32 에서 Mac 과
  pod 가 바이트까지 같은 INT8 을 만든다(R2 `cd6e3fca…`).
- R1 을 이 양자화로 다시 재도 R1 첫 pod 수치가 그대로 나왔다 — O1 0.342 / 0.007(같음), KDPII
  0.9793 / 0.9765(이전 0.9795 / 0.9768), bc 오탐 63행(이전 62). 7비트 가중치의 비용은 VNNI · ARM 에서 잴
  수 없을 만큼 작다.
- 배포 INT8 을 바꿨다: `train/artifacts/l1-onnx/garim_l1.int8.onnx` = `906f73ec…`. 원래 파일은
  `garim_l1.int8.u8s8.onnx`(`a0e39349…`)로 남겼다 — RESULTS_L1 의 R1 수치는 그 파일로 잰 것이다.
- PREREG §8 마지막 행에 판정 지표를 보기 전에 벗어남으로 적었다.

### ② 컨테이너 CPU 할당량을 넘는 onnxruntime 스레드

pod 는 64코어가 보이지만 cgroup 할당량이 8.5코어였다. onnxruntime 이 보이는 물리 코어(32)만큼 스레드를
잡아 throttling 을 반복했고, 첫 평가는 2시간이 지나도록 INT8 한 번을 못 끝냈다. 같은 pod 40행 기준
기본 346.9ms/행 → 8스레드 35.7ms/행, 출력은 같다. 행당 수백 ms 면 검사기의 L1 예산(1,000ms)을 꼬리
행이 넘어 판정이 부하에 따라 바뀐다. `OnnxL1Backend` 가 `GARIM_L1_THREADS` → cgroup 할당량 순으로
스레드를 정한다(커밋 `af350c4`). 판정 측정의 KoreanGuardrail 은 28~29ms/행이었다.

## 5. 판정에 쓰면 안 되는 수치

- **O2 · O3 의 0.000 절대값** — 자작 family 다. 방향과 bc-pii-ko(외부)만 쓴다.
- **검증 hmean 0.762 와 R1 의 0.922 비교** — 검증 셋이 다르다(R2 는 처음 보는 공격 템플릿을 담았다).
- **`train/artifacts/r2/defective-u8s8/`** — 결함 커널로 잰 R1 값이다. 결함의 증거로만 쓴다.
- **INT8 · FP32 대조와 지연** — 판정 pod 에서 재지 않았다(할당량 throttling · 판정 지표 우선). 지연은
  노트북에서 다시 잰다.

## 6. 산출물

| 위치 | 무엇 |
|---|---|
| `train/artifacts/l1-r2/` | seed 별 체크포인트 · 학습 보고서 · `three-seed-report.json` |
| `train/artifacts/l1-r2-onnx/` | FP32 · INT8(`reduce_range`, 판정에 쓴 것) · INT8 원본(`.u8s8`) |
| `train/artifacts/l1-onnx/` | **배포 R1** — INT8 이 `reduce_range` 판으로 바뀌었다 |
| `data/runpod/2026-10-27-r2/` | 교사 원출력 · 합의 · 봉인 manifest · pod 로그 · `final/train/artifacts/r2/`(판정표 `verdict.json`, 지표 원본) |
| HF `cycloevan/garim-guard`(model) | 위 모델 전부 · Gemma 4 L2 GGUF. 구 SafeAgent-KR 은 `legacy-safeagent` 브랜치 |
| HF `cycloevan/garim-guard`(dataset) · `cycloevan/garim-guard-private` | `data/` · R1 결과 JSON. 합성 주민번호 파일 · 원천 원본은 비공개 저장소 |

모든 회수 파일은 pod 와 sha256 을 대조했다. `data/` 에는 체크섬을 통과하는 합성 주민등록번호가 있어
Git 밖에 둔다.
