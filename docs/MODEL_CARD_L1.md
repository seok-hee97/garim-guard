---
language: [ko]
library_name: onnxruntime
base_model: skt/A.X-Encoder-base
license: cc-by-sa-4.0
tags: [token-classification, prompt-injection, pii, korean]
---

# Garim L1 joint encoder — 2026-10-27

> `train/MODEL_CARD_TEMPLATE.md` 를 이번 학습으로 채운 것. 측정하지 않은 칸은 비워 두고 그렇다고 적었다.
> 수치의 근거와 유보는 [RESULTS_L1.md](RESULTS_L1.md).
>
> **갱신(2026-10-27)** — ① 배포 INT8 을 `reduce_range=True` 로 다시 양자화했다(가중치는 같다). 원래 INT8 은
> VNNI 없는 x86(AMD Zen2 · Zen3, Ice Lake 이전 Intel)에서 int16 포화로 판정이 깨졌다. ② 2차 학습(R2)은
> 사전 등록 판정에서 O1 · O4 미달로 채택되지 않았다 — 이 카드의 모델이 계속 배포 모델이다.
> 둘 다 [RESULTS_L1_R2.md](RESULTS_L1_R2.md).

## 모델

- 백본: `skt/A.X-Encoder-base@9708f9c404ace91efd25c06fac2d73413616f4ef`
- 구조: 공유 encoder + PII BIO 29-way + 공격 4-way + 이진 보조 헤드 2개(로마자 우회 · 존댓말 사회공학)
- 학습: 3 seed(13 · 37 · 73) × 5 epoch, 논리 배치 32(micro-batch 16 × 2), bf16 autocast,
  RunPod RTX 3090, 약 2시간 40분
- **배포 seed: 37** — 검증 `best_joint_recall_hmean` 최댓값(0.9219, epoch 3). 기준은 학습 전에
  RUNPOD.md 11단계에 적어 둔 그대로다. seed 간 차이(0.896 / 0.922 / 0.918)는 검증 소수 클래스
  (JAILBREAK 13 · SYSTEM_LEAK 8행)로 설명되는 크기라 37 이 본질적으로 낫다는 근거는 아니다.
- 코드: 학습 `6a4b841`, 데이터 준비 `ebbb7a0`
- 산출물: `garim_l1.int8.onnx`(152MB, QInt8 per-channel · `reduce_range`, sha256 `906f73ec…`) ·
  `garim_l1.fp32.onnx`(+ `.data`, 595MB) · 토크나이저 · `labels.json`. 첫 수치를 낸 원래 INT8 은
  `garim_l1.int8.u8s8.onnx`(`a0e39349…`)로 남겼다. 아래 PII 제품 경로 · 공격 탐지 수치는 새 판으로 다시 잰
  값이고(RESULTS_L1_R2 §0 의 R1 열), 괄호 안이 원래 파일 값이다
- 저장: HF `cycloevan/garim-guard`(model) `l1/r1/`

## 학습 데이터와 고지

| 데이터 | 용도 | 고정 revision/해시 | 라이선스 |
|---|---|---|---|
| KDPII revised | PII train/dev/test 연결성분 분할 | `4017f68284596ffc357dd23363fb9ab9eb2eb730f01973af8beded54430d3e74` | CC-BY-4.0 |
| KLUE-NER train | 필터된 PS→PERSON 학습 | `349481ec73fff722f88e0453ca05c77a447d967c` | CC-BY-SA-4.0 |
| PolyGuardMix ko | 교사 재라벨 공격 학습(20,000행 표본) | `5b7d93e9e9a636c6c152df071745e04130a21ff4` | CC-BY-4.0 |
| deepset prompt-injections train | 분할 후 한국어 번역·교사 재라벨 | `4f61ecb038e9c3fb77e21034b22511b523772cdd` | CC-BY-4.0 |
| bc-pii-ko | 값 사전만 사용 | `327c775b68850ca59cf4ff2ad0b084fcd4916da4` | CC-BY-4.0 |
| Garim synthetic | PII/공격 보강 | 생성기 버전 미기재 — `data/corpus/preparation-report.json` 이 산출 분포를 기록한다 | 프로젝트 생성 |

봉인: 학습 130,221 레코드(`data/training-input-manifest.json`), 평가 17,146 레코드
(`data/evaluation-input-manifest.json`). PII 병합 sha256 `0d62d35e…`(train) · `213d9685…`(validation),
교사 입력 `fc305d71…`.

**가중치 라이선스: CC-BY-SA-4.0.** 학습 데이터에 KLUE-NER(CC-BY-SA-4.0)가 들어 있다. 학습된 가중치가 그
데이터의 2차적 저작물인지는 법적으로 정리되지 않았지만, ShareAlike 가 전파된다고 보는 보수적 해석을 따랐다
(KLUE 팀의 `klue/bert-base` 도 CC-BY-SA-4.0 이다). 상업적 이용 · 수정 · 재배포가 가능하고, 출처를 표시하며, 가중치를
고쳐 배포하면 같은 라이선스로 배포한다. 코드는 Apache-2.0, L2 GGUF 는 원 모델(`google/gemma-4-E2B-it`)의
라이선스를 따른다.

## 교사

- `kakaocorp/kanana-safeguard-prompt-2.1b@167d74d4706b236580b0e48318337c7ac6ba7848`
- `google/gemma-4-E2B-it@3e22461f65e89153144f8adb70e3b8c2cc9845a7`
- `Qwen/Qwen3Guard-Gen-4B@6ec42827da0c1ff11e7a49dc269d2e810d27e108` — Gen-0.6B 에서 교체.
  0.6B 의 생성기 JAILBREAK 재현율이 0.145 여서 합의 JAILBREAK 가 53행으로 굶었다. 교사 산출물을 본 뒤·
  학습 전에 내린 결정이다(RUNPOD.md 6b).

일반 다수결이 아니라 클래스별 두 교사 합의를 사용했습니다. **사람 검수는 하지 않았고**, 불일치·
누락 행은 `NONE` 으로 흡수하지 않고 감사 기록과 함께 학습에서 뺐습니다(`--drop-review`) —
19,157행 중 14,020 수용 · 5,137 폐기. 합의 학습 행: NONE 11,815 · INJECTION 633 · JAILBREAK 132 ·
SYSTEM_LEAK 75.

## 결과

봉인 test, ONNX QInt8. FP32 와의 차이는 모든 칸에서 ±0.002 이내(공격 macro recall −0.0002).

| 지표 | seed 3개 검증 평균 ± 표준편차 (FP32) | ONNX QInt8 test (seed 37) | 평가 근거 |
|---|---:|---:|---|
| PII 문자 recall | 0.997 ± 0.001 | KDPII 0.977 · KLUE PERSON 0.931 (0.932) | **제품 경로**(임계·병합 후). 원출력은 0.985 · 0.933 |
| PII 문자 precision | 0.866 ± 0.039 | KDPII 0.979 (0.980) · KLUE PERSON 0.890 (0.889) | 같은 |
| PII 오탐 행 비율 | — | PII 아닌 숫자 문장 8.8% (8.6%) · T4 0.47% | 제품 경로 |
| 공격 macro recall / F1 | 0.840 ± 0.024 / 0.884 ± 0.015 | T4 0.605 / 0.618 · 합성 0.765 / 0.761 | 자작 셋 |
| 공격 탐지율 / 과방어율 | — | **0.342 / 0.007** | KoreanGuardrail 5,555 (외부). 교사와 같은 600 표본에서는 0.330 / 0.003(배포 파일, RESULTS_L1_R2 S5 · 원래 INT8 은 0.327) — kanana 단독 0.630 / 0.017 |
| 공격 FPR (NONE→공격) | 0.0018 ± 0.0005 | T4 0.169 · 합성 0.122 · deepset 0.000 | T4 · 합성의 NONE 오탐은 전부 SYSTEM_LEAK |
| 128/256/512 토큰 p50 | — | 99 / 138 / 408 ms | **pod CPU**(16코어 컨테이너) · 원래 INT8 파일. 노트북 미측정 |
| 128/256/512 토큰 p95 | — | 104 / 245 / 678 ms | 같은 |
| 파일 크기 / RSS | — | 152MB / 937MB(로드 후) | 같은 |

seed 평균은 **검증** 값이다 — test 는 배포 seed 하나만 쟀다. 합성 PII test 의 1.000 은 구현 회귀
지표이며 실분포 재현율의 증거로 주장하지 않습니다.

제품 경로 값은 L0 규칙에 따라 바뀐다. 같은 파일을 0.1.3 규칙으로 Apple M1 에서 다시 잰 값은 KDPII
0.988 / 0.985, KLUE PERSON 0.892 / 0.931, KoreanGuardrail 0.349 / 0.007(600 표본 0.340 / 0.003)이다.
L1 원출력 재현율(0.985 · 0.933)은 위와 같다 — [RESULTS §10](RESULTS.md).

## 알려진 한계

- **합의가 하드 네거티브를 지웠다.** 생성기 NONE 954행(기본 문장 5개의 변주) 중 379행이 교사
  불일치로 폐기됐다 — 트리거 단어를 품은 두 문장이 통째로 빠졌다. NONE→SYSTEM_LEAK 오탐의 대부분이
  그중 "시스템 설정 화면을 여는 방법" 에서 나온다(RESULTS_L1 §5-②).
- **합성 test·T4 는 일반화 지표가 아니다.** 생성기의 `template_id` 가 실제 템플릿이 아니어서 test 의
  기본 문장이 전부 학습에도 있다(RESULTS_L1 §5-③). 일반화 근거는 KoreanGuardrail · deepset 뿐이다.
- **12자리 운송장번호를 계좌번호로 가린다** — PII 아닌 숫자 문장 720행 중 63행(8.8%)에서 오탐이 나왔고 대부분이
  12자리 운송장번호다(원래 파일 Mac 재측정 60건 중 54건).
- **SYSTEM_LEAK 이 약하다** — 학습 75행, KoreanGuardrail A2 탐지율 0.146.
- 정규화가 되돌리지 않는 변이(띄어쓰기 깨기·초성·경음화)에서 탐지율이 clean 0.541 → 0.108~0.236.
- 영어 텍스트의 문맥 개체는 거의 못 본다(TAB DIRECT 0.036).
- KDPII에 건강보험번호가 없어 `KR_NHI` 외부 실값 재현율 근거가 없습니다.
- KLUE PS는 유명인 중심이며 일반 고객·직원 성명 분포를 대표하지 않습니다.
- bc-pii-ko 문맥은 합성 번역체이므로 값만 사용했습니다(음성 평가는 별도).
- RRN 및 일부 국내 식별자는 적법한 비생성기 실값 근거가 없습니다.
- L2는 이 모델에 포함되지 않습니다.
- **2차 학습으로 오탐을 줄이면 탐지가 같이 줄었다** — R2 는 처음 보는 정상 문장 오탐 0.439 → 0.000,
  bc 음성 오탐 63 → 0 행을 냈지만 KoreanGuardrail 탐지율 0.342 → 0.316, KLUE PERSON 재현율 0.931 → 0.906
  으로 내려가 채택되지 않았다(RESULTS_L1_R2 §3). 이 카드의 오탐 한계는 그대로 남아 있다.
