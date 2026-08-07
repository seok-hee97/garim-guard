# L2 축별 평가 현황

> 기준일: 2026-08-07
> 범위: L2 는 파인튜닝하지 않는다(§6.4). 여기 적힌 것은 전부 **학습 없이** 얻은 것이다.

§6.4.1 이 L2 를 네 축으로 나눴다. 축마다 (1) 목적에 맞는 모델이 있는가 (2) 공개
평가셋이 있는가 (3) 실측했는가가 다르다. 이 문서는 그 셋을 축별로 분리해 적는다.

## 요약

| 축 | 목적 전용 모델 | 공개 평가셋 | 실측 |
|---|---|---|---|
| **(a)** PII 문맥 | △ 있으나 미채택 — `kanana-safeguard-siren-8b` 은 document-level 이라 스팬을 안 준다 | ✅ **TAB** (MIT) | ❌ |
| **(b)** 공격 의도 | ✅ `kanana-safeguard-prompt-2.1b` — 카테고리가 Prompt Injection/Leaking 이라 정확히 대응 | ✅ **KoreanGuardrail** 외 3종 | ✅ **아래** |
| **(c)** 기밀 표식 | ❌ 없음 | ❌ **없음** — 자체 제작 불가피 | L0 만 |
| **(d)** 시크릿 플레이스홀더 | ❌ 없음 (§6.4.5: 모델이 아니라 파이프라인) | ✅ **CredData** (Apache-2.0) | ❌ |

축 (a)(c)(d) 는 §6.4.3 대로 Qwen3Guard-Gen-4B + 축별 정책 프롬프트로 간다. 즉
**"목적에 맞는 모델이 있어서 학습이 필요 없다"가 성립하는 것은 축 (b) 하나뿐**이고,
나머지 셋은 "맞는 모델이 없지만 파인튜닝 대신 범용 judge + 프롬프트로 간다"는
§6.4.2 의 결정이다. 둘을 같은 문장으로 쓰지 않는다.

## 축 (b) 실측 — `kimchunsik03/KoreanGuardrail`

5,555 행(공격 3,311 / 정상 2,244). 지표 정의는 데이터셋 카드를 따른다 —
`evasion_rate = 1 - 탐지율`, `over_defense_rate = 정상 행 오탐률`.
하네스는 [`bench/ko_guardrail.py`](../bench/ko_guardrail.py), 실행은
`python -m bench.run_axis_b`.

| 판정자 | 표본 | 탐지율 | 과방어율 | ms/행 |
|---|---|---|---|---|
| 규칙만 (정규화 OFF) | 5,555 | 0.027 | 0.005 | 0.0 |
| **가림 L0** (정규화 ON) | 5,555 | **0.048** | 0.005 | 0.2 |
| PolyGuard-Qwen-Smol | 120 | 0.183 | 0.150 | 1,445 |
| **kanana-safeguard-prompt-2.1b** | 200 | **0.620** | **0.020** | 321 |

측정 환경은 M1 / 16GB / MPS, 모델은 fp16 비양자화다. §6.4.6 의 130ms 게이트는
Q4 GGUF 단일 토큰 기준이므로 위 321ms 와 직접 비교하지 않는다.

### 기법별 — §2.2 은닉 축과 대응한다

| technique | 가림 L0 | 정규화 OFF | kanana | 대응 코드 |
|---|---|---|---|---|
| `clean` | 0.060 | 0.060 | **1.000** | — |
| `jamo_decompose` | 0.060 | 0.007 | 0.900 | A-KO-01 |
| `zwsp_inject` | 0.060 | 0.010 | 0.909 | A-ENC-02 |
| `break_spacing` | 0.057 | 0.057 | 0.842 | A-KO-05 |
| `chosung` | 0.045 | 0.033 | **0.125** | A-KO-02 |
| `tensify` | 0.012 | 0.010 | 0.389 | (A-KO-07 로 신설) |

난독화 강도별로 kanana 는 `0 → 1.000`, `0.5 → 0.689`, `1.0 → 0.500` 으로 무너진다.

### 읽는 법

**가림 L0 의 0.048 은 결함이 아니라 바닥선이다.** §2 는 L0 에 고정밀 문구만 맡기고
공격 축을 L1/L2 로 넘긴다. `clean` 에서도 0.060 인 것이 그 설계의 직접적인 결과다.
이 값의 용도는 L1/L2 를 붙였을 때의 **증분을 재는 기준선**이다.

**정규화 체인의 효과는 여기서 이미 보인다.** 전체 0.048 vs 0.027 이고, 기법별로
`jamo_decompose` 0.060 vs 0.007(8.6배), `zwsp_inject` 0.060 vs 0.010(6배)이다.
강도별로 정규화 ON 은 `0.060 / 0.045 / 0.048` 로 평탄하고 OFF 는
`0.060 / 0.020 / 0.027` 로 무너진다 — §9-2 가 주장하는 것이 학습 0 으로 측정된다.

**정규화 체인이 실제로 되돌리는 것은 둘뿐이다.** 5,555 행 전수로 확인했다.

| technique | 정규화가 원문을 복원한 비율 |
|---|---|
| `jamo_decompose` | 99.8% |
| `zwsp_inject` | 99.8% |
| `break_spacing` | 99.8% (`norm_nospace` 스트림에서) |
| **`chosung`** | **0.2%** |
| **`tensify`** | **0.2%** |

위 표는 이 측정 시점의 값이다. 초성체·된소리화는 복원 대상이 아니었고, 그 구멍을
아래 「초성체·된소리화」 절에서 닫았다.

**kanana 의 초성체 0.125 는 여전히 열려 있다.** 한국어 전용 SOTA 가드가 `clean`
1.000 에서 `chosung` 0.125 로 떨어진다. 가림의 초성 서명이 L0 사전 범위에서는
89% 를 회복했으므로, **L2 앞단에 초성 정규화를 두면 kanana 의 이 구멍을 메울 수
있는지**가 다음 측정 대상이다.

### 정규화 전처리 ablation — 아직 판정 불가

kanana 앞에 정규화 체인을 붙여 200행 표본으로 대조했다. 전체 탐지율 0.620 →
0.620, 기법별 전부 ±0.000 이었다.

⚠️ **"효과 없음" 으로 읽으면 안 된다.** 배선을 직접 확인한 결과 `jamo_decompose`
10행 중 2행의 판정이 실제로 뒤집혔고, 다만 **양방향이라(하나는 미탐→탐지, 하나는
탐지→미탐) 합계가 상쇄됐다.** 표본 200행이면 기법당 n≈10~20 이라 해상도가 ±0.1 이다.
정규화가 이미 복원하던 두 기법에서 kanana 가 이미 0.90 대라 개선 여지도 작았다.
**초성 서명이 들어간 지금 전건 5,555행으로 다시 재야 한다** (약 30분) — 초성체
0.125 가 움직이는지가 핵심이다.

## PolyGuard 를 L2-fast 후보에서 기각한 이유

라이선스(CC-BY-4.0)·한국어 포함 17개 언어·GGUF 공개까지 조건이 다 맞아
[`models.yaml`](../train/models.yaml) 에 올릴 뻔했으나, **재는 축이 다르다.**

PolyGuard 의 택소노미는 S1~S14 harm 카테고리(폭력·범죄·혐오 등)다. KoreanGuardrail
은 "이전 지시 무시"·"시스템 프롬프트 출력" 같은 메타 지시문이고 유해 콘텐츠를
의도적으로 뺀 셋이다. 실측에서 탐지율 0.183 / 과방어율 0.150 으로 **판별력이 거의
없었고**, `A2_leaking` 은 0/19 였다. 첫 출력이 `Request safety violations: S7`
(Privacy) 인 것이 원인을 그대로 보여준다 — 맞는 라벨이 없어 가장 가까운 것을 고른다.

§5.2 가 PolyGuardMix 어댑터에 *"source harm labels are not project attack labels"*
라고 적어둔 것과 같은 이유이고, 모델이 그 택소노미를 그대로 물려받았다.
→ **`purpose: baseline` 로 등록한다.** 기성 다국어 SOTA 가드가 한국어 인젝션에서
무너지는 증거로는 §9-2 에 값이 있다.

## kanana 의 transformers 충돌 — 해소했다

`kakaocorp/kanana-safeguard-prompt-2.1b` 는 `hidden_size 1792` 에
`num_attention_heads 24`, `head_dim 128` 을 명시한 decoupled head 구성이다.
`transformers 5.14.1` 의 `validate_architecture` 가 `head_dim` 을 보지 않고
`hidden_size % num_attention_heads` 만 검사해 **`AutoConfig.from_pretrained` 단계에서
막힌다.**

```
StrictDataclassClassValidationError: validator 'validate_architecture':
  The hidden size (1792) is not a multiple of the number of attention heads (24).
```

**설정이 유효하고 검증자가 틀렸다** — `head_dim` 을 명시해 hidden 과 헤드 수를 분리한
구성은 Llama 3.2·Gemma 가 쓰는 정상 패턴이다.

영향 범위가 평가에 그치지 않았다. 같은 모델이 `teacher_kanana` 로 L1 증류 교사에
고정돼 있고(§6.2.2) L2-fast 채택 모델이기도 하다(§6.4.3) — GPU 박스의
`run_teacher.py --teacher kanana` 가 같은 지점에서 실패했을 것이다.

→ [`garim/training/compat.py`](../garim/training/compat.py) 로 해소했다. `head_dim` 이
선언돼 있고 검증자가 거부할 구성일 때만 `validate_architecture` 를 한시적으로 걷어내고,
컨텍스트를 벗어나면 복원한다. transformers 5.14.1 에서 로드·복원 모두 확인했다.
`teacher_inference.py` 와 `bench/judges.py` 가 이 경로를 쓴다.

## 축별 남은 일

- **(a)** TAB 을 받아 `identifier_type` DIRECT/QUASI/NO_MASK 3분류로 평가 하네스 작성
- **(b)** 전건 5,555행 정규화 ablation · Q4 GGUF 지연 게이트 (초성·된소리는 아래대로 처리됨)
- **(c)** 평가셋 자체 제작 (§8.1 8d) — 공개 자산이 존재하지 않는다
- **(d)** CredData 로 플레이스홀더 판별 평가 — 음성 69,259 줄
- 공통: ~~transformers 버전 충돌 해소~~ (완료)

## 초성체·된소리화 — 은닉 축 둘을 닫았다

측정이 지목한 두 구멍을 구현했다.

- **A-KO-02 초성체** — 초성화는 가역이 아니라 복원 대신 **양쪽을 같은 초성 공간으로
  내려 비교**한다([`normalize/chosung.py`](../garim/normalize/chosung.py)). 기존
  `_CHOSEONG` 은 `ㅁㅅㅎ|ㅇㅈㅈㅅ|ㅍㄹㅍㅌ` 의 **연속** 매칭이라 `ㅇㅈ ㅈㅅ는 ㅈ부 무시하고`
  처럼 사이가 벌어진 실제 난독화를 전부 놓쳤다. 맨 자음이 있고 **맨 모음은 없을 때만**
  켠다 — 그 조건이 없으면 자모 분해가 초성체로 오인돼 A-KO-01 이 A-KO-02 로 바뀐다.
- **A-KO-07 된소리화** — 음절 초성의 된소리·거센소리를 평음으로 내린 `detensified`
  스트림을 추가했다(`이쩐 찌시는 전뿌` → `이전 지시는 전부`). 손실 변환이라 `norm`
  본류를 오염시키지 않고 `nospace` 와 같이 별도 스트림 + 신뢰도 감쇄로 둔다.
  §2.2 에 대응 코드가 없던 축이라 A-KO-07 을 신설했다.

**L0 자기 사전 커버리지(18 시드) 안에서 난독화 내성:**

| technique | 이전 | 지금 |
|---|---|---|
| `jamo_decompose` | 100% | 100% |
| `zwsp_inject` | 100% | 100% |
| `break_spacing` | 94% | 94% |
| **`chosung`** | ~0% | **89%** |
| **`tensify`** | 19% | **47%** |
| **전체** | 81% | **86%** |

벤치 전체 탐지율은 0.048 → 0.073 이고 **과방어율은 0.005 → 0.004 로 오히려 내려갔다.**

⚠️ 패턴은 기존 `attacks.py` 키워드 사전에서만 유도했고 벤치마크 행을 보고 맞추지
않았다(§7.6-1). 사전에서 유도한 케이스 12건에서 재현율 100% / 오탐 0% 다.
`tensify` 47% 가 낮은 것은 된소리화가 초성뿐 아니라 중성까지 흔드는 경우가 있어서다.

## 사람 검수 없는 운영 — 두 자리를 고정했다

- **교사 불일치** → `resolve_teacher_labels.py --drop-review`. 설계가 금지한 것은
  `NONE` 흡수이지 폐기가 아니다. 폐기 건수와 경로를 감사 파일에 남긴다.
- **최종 test gold** → [`external_labels.py`](../garim/training/external_labels.py).
  택소노미가 1:1 인 외부 라벨만 받는다. deepset 의 `injection`/`legitimate` 는 받고,
  PolyGuard 의 harm 라벨은 **거부**한다(추측하면 헤드라인이 조용히 움직인다).
  교사 합의 라벨은 순환이라 쓰지 않는다(§7.6-3).

→ 사람 없이 확보되는 외부 gold 는 **인젝션 축의 이진 판정뿐**이다. 4-way macro 는
구성상 정답이 알려진 합성 test 로만 보고하고, **T3 수동 골드셋(200건)은 미수행**으로
§7 에 남긴다. 이것이 (B) 종착점에서 잃는 것이다.

## 재현

```bash
python -m bench.run_axis_b \
  --benchmark data/raw/KoreanGuardrail/benchmark.jsonl \
  --judge garim-l0 --judge rules-no-normalization \
  --output results/axis-b.json

# kanana 는 `training/compat.py` 의 shim 을 타므로 transformers 5.x 에서도 돈다
python -m bench.run_axis_b --benchmark ... \
  --judge kanana --kanana-dir models/pinned/teacher_kanana --sample 200

# 라이선스 게이트 — HF 를 런타임 조회해 매니페스트와 대조한다 (네트워크 필요)
python train/license_check.py
```
