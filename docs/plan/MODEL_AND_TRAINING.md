# 모델·시스템 구성 및 학습 계획

> 2026-08-01 · [기획서](기획서.md) §4의 상세 · [AGENT_SYSTEM](AGENT_SYSTEM.md)의 모델 층 구현 계획
>
> 이 문서는 **배포 모델을 로컬 우선(local-first)으로 재정의**하고, 그로부터 따라오는 모델 선정·학습 계획을 확정한다.

---

## 1. 배포 모델 재정의 — 로컬 우선

### 1.1 앞선 설계의 모순

지금까지 문서는 게이트웨이를 **서버에 배포하는 것**으로 전제했다. 그런데 프로젝트의 핵심 논지는 "탐지는 로컬에서, 생성만 외부로"다. 게이트웨이가 서버에 있으면 **그 서버 운영자는 원문 PII를 본다.** 논지가 절반만 성립한다.

그리고 서버 배포를 전제하는 순간 멀티테넌시·OAuth Resource Server·정책 배포·HA가 전부 필요해지는데, 이건 이미 범위 밖으로 밀어낸 것들이다. **범위 밖 목록이 길어지는 게 아니라, 애초에 필요 없는 구성을 골라야 한다.**

### 1.2 결정: 클라이언트 PC에서 도는 단일 바이너리

```
┌─────────────────── 클라이언트 PC (사용자 노트북) ───────────────────┐
│                                                                 │
│   앱 · 에이전트 · IDE · CLI                                        │
│        │ OpenAI 호환  http://localhost:8080/v1                   │
│        │ MCP          stdio 또는 http://localhost:8081/mcp        │
│        ▼                                                        │
│   ┌─────────────────────────────────────────┐                   │
│   │  가림 로컬 게이트웨이 (단일 프로세스)          │                   │
│   │                                         │                   │
│   │  L0  정규식 + 체크섬        (CPU, <10ms)  │                   │
│   │  L1  PII NER              (CPU, ~40ms)  │                   │
│   │  L1  인젝션 분류기          (CPU, ~15ms)  │                   │
│   │  볼트 · taint · trifecta   (인메모리)     │                   │
│   │  MCP 게이트웨이             (프록시)       │                   │
│   │  감사 로그                  (로컬 JSONL)   │                   │
│   └─────────────────────────────────────────┘                   │
│        │                                                        │
│        │ ★ 이 경계를 넘는 것은 마스킹된 프롬프트뿐 ★                    │
└────────┼────────────────────────────────────────────────────────┘
         ▼
   Gemini API · OpenAI API · (또는 로컬 Gemma 4 E2B — 완전 오프라인)
```

**이 구성이 주는 것**

| | 서버 게이트웨이 | **로컬 게이트웨이** |
|---|---|---|
| 원문 PII를 보는 주체 | 게이트웨이 운영자 + 사용자 | **사용자 본인뿐** |
| 배포 | 인프라 구축 필요 | `pip install` → `garim run` |
| 데모 재현성 | 클라우드 계정 필요 | **누구나 노트북에서** |
| 브리프의 "on-device model" | 서버의 온프렘일 뿐 | **문자 그대로 온디바이스** |
| 검증 방법 | 로그를 믿어야 함 | **패킷 캡처로 증명 가능** |
| 조직 차원 강제 | 가능 | 약함 (사용자가 끌 수 있음) |

마지막 행이 유일한 손해인데, **그건 정직하게 인정하고 범위에 명시한다.** 이 제품은 "IT가 전사에 강제하는 통제"가 아니라 "개발자·분석가가 자기 PC에서 켜는 안전장치"다. SecureBridge가 전자라면 이건 후자이고, 후자가 솔로 5주에 완결 가능한 유일한 형태다.

### 1.3 같은 바이너리, 두 실행 모드

코드는 하나다. 어디서 돌리고 설정·감사를 어디에 두느냐만 다르다.

| 모드 | 실행 위치 | 정책 | 감사 | v1 |
|---|---|---|---|---|
| **`local`** (기본) | 사용자 PC | 로컬 YAML | 로컬 JSONL | ✅ 구현 |
| `team` | 사내 서버 | 원격 정책 fetch | 중앙 수집 | 설계만, 미구현 |

`team` 모드는 코드 구조로만 열어 둔다(정책 로더와 감사 싱크를 인터페이스로 분리). **구현하지 않은 것을 구현한 척하지 않는다.**

### 1.4 이 결정이 모델 선정을 규정한다

노트북 CPU에서 돌아야 하므로 모델 예산이 하드 제약이 된다. 실제로 계산해 봤다.

| 모델 | 전체 | 활성 | bf16 | int8 | 판정 |
|---|---|---|---|---|---|
| `openai/privacy-filter` (sparse MoE) | 1.5B | 50M | 3.0GB | 1.5GB | **활성은 50M이지만 128 experts가 전부 상주** |
| `skt/A.X-Encoder-base` | 0.1B | 0.1B | 0.2GB | 0.1GB | 가볍다 |
| `kanana-safeguard-prompt-2.1b` | 2.1B | 2.1B | 4.2GB | 2.1GB | **인라인 불가** |

가드만 올려도 kanana를 포함하면 bf16 기준 **7.4GB**다. 노트북에서 다른 작업과 공존할 수 없고, 2B 디코더의 CPU 추론은 레이턴시 예산(입력 p50 < 100ms)도 못 맞춘다.

> **그래서 kanana는 인라인에 두지 않는다. 교사(teacher)로만 쓰고, 0.1B 인코더로 distillation 한다.**
> 이것이 §5의 학습 과제 R2가 **선택이 아니라 로컬 실행의 전제**인 이유다.

---

## 2. 시스템 구성 — 무엇이 어디서 도는가

### 2.1 계층별 배치

| 계층 | 컴포넌트 | 위치 | 학습 | 메모리 | 예산 |
|---|---|---|---|---|---|
| **L0** | 한국어 정규식 8종 + 체크섬 | 로컬 CPU | ❌ 규칙 | ~0 | < 10ms |
| **L1-PII** | Korean PII NER | **로컬 CPU** | ✅ **R1** | 1.5GB(int8) 또는 0.1GB | ~40ms |
| **L1-INJ** | 인젝션 분류기 | **로컬 CPU** | ✅ **R2** | 0.1GB | ~15ms |
| **L2** | 응답 스캔 + 볼트 복원 | 로컬 CPU | ❌ | — | < 20ms |
| **L3** | 심층 감사 라벨링 | **로컬, 비동기** | ❌ | 2.1GB(int8) | 무제한 |
| **업스트림** | 생성 LLM | **API 또는 로컬** | ❌ | — | — |

**L3를 비동기로 뺀 것이 로컬 실행의 핵심 트릭이다.** kanana-8b/siren-8b는 GPU가 있을 때만, 그리고 요청 경로 밖에서 배치로 돈다. GPU가 없는 노트북에서는 아예 비활성화해도 L0~L2는 온전히 동작한다.

### 2.2 하드웨어 프로파일

성능을 보고할 때 **어느 프로파일에서 쟀는지 반드시 명시한다**(평가 원칙 5조).

| 프로파일 | 사양 | 구성 |
|---|---|---|
| **P1 — 최소** | 노트북 CPU, RAM 8GB | L0 + L1-INJ(0.1B). PII NER은 정규식만 |
| **P2 — 표준** | 노트북 CPU, RAM 16GB | L0 + L1 전체 (int8) + API 업스트림 |
| **P3 — 오프라인** | RAM 32GB 또는 GPU | P2 + Gemma 4 E2B 로컬 업스트림 → **외부 통신 0** |
| **P4 — 학습** | RTX 4090 24GB (RunPod) | R1·R2 학습 전용. 런타임과 무관 |

P3가 데모의 하이라이트다. 네트워크를 끊고도 에이전트가 동작하고, PII가 마스킹되고, 도구 호출이 차단되는 것을 보인다.

### 2.3 업스트림 연결

| 업스트림 | 용도 | 주의 |
|---|---|---|
| **Gemini API** (`gemini-3.6-flash`) | 기본 | ⚠️ **free tier는 데이터가 학습에 쓰인다 — 실제 프롬프트에 금지.** 유료 tier 필수 |
| OpenAI API | 호환성 확인용 | |
| **Gemma 4 E2B 로컬** (llama.cpp/Ollama) | P3 오프라인 | Apache 2.0. CPU 5~10 tok/s |

LiteLLM이 프로바이더 추상화를 담당하므로 추가 프로바이더는 설정만으로 붙는다.

---

## 3. 모델 사용 — 계층별 선정과 근거

### 3.1 L1-PII — 두 트랙 병행 후 선택

정규식(L0)이 형식 완전한 식별자를 높은 정밀도로 잡는다면, NER은 **정규식이 원리적으로 못 잡는 것**을 담당한다.

| 정규식이 못 잡는 것 | 예 |
|---|---|
| 조사가 붙은 한국식 성명 | `김철수님께서`, `이상무가` (인명 vs 직책 중의성) |
| 도로명·지번 주소 | `서울시 강남구 테헤란로 152` |
| 문맥 의존 식별자 | `제 계좌는 국민 123456-78-901234 입니다` |
| 자연어로 풀어 쓴 번호 | `구공공일공일에 다시 일이삼사...` |

두 트랙을 W3까지 병행 비교한 뒤 하나를 고른다.

| | **트랙 A — A.X-Encoder-base 풀 FT** | **트랙 B — privacy-filter LoRA** |
|---|---|---|
| 베이스 | `skt/A.X-Encoder-base` (0.1B, ModernBERT) | `openai/privacy-filter` (1.5B/50M active) |
| 라이선스 | **Apache 2.0** | **Apache 2.0** |
| 컨텍스트 | **16,384** | 128,000 |
| 메모리(int8) | **0.1GB** | 1.5GB |
| 방식 | 풀 파인튜닝 | LoRA r=16, MoE 프리즈 |
| 장점 | 가볍고 빠르다. 노트북 P1에도 들어간다 | base가 PII 스팬 개념을 이미 안다 → 데이터 효율 |
| 단점 | 처음부터 PII를 배워야 함 | 128 experts 전부 상주 → 메모리 15배 |
| 선행 사례 | 없음 (토큰 분류 벤치마크 미공개) | **`FrameByFrame/privacy-filter-korean`이 검증** |

**선행 사례가 중요하다.** `FrameByFrame/privacy-filter-korean`이 정확히 트랙 B를 했고 레시피를 공개했다 — LoRA r=16, attention projection만(`q/k/v/o_proj`), **sparse-MoE 백본 프리즈**, 학습 파라미터 614k(전체의 0.04%), 10 epochs, AdamW lr 5e-4, KDPII + 합성 RRN. 결과 validation F1 0.848.

⚠️ **다만 그 수치를 목표로 삼지 않는다.** 공개된 split이 5/5/90 행 단위로 보이는데, 템플릿 기반 데이터에서 행 단위 split은 낙관 편향을 만든다(§6.2). 우리는 템플릿 단위로 다시 재고, **두 수치를 모두 보고**한다.

**로컬 우선 관점의 기울기**: 트랙 A가 메모리 15배 유리하다. 트랙 B가 F1에서 유의하게 이기지 않으면 A를 택한다. 판단은 W3 게이트.

### 3.2 L1-INJ — distillation이 필수인 이유

`kanana-safeguard-prompt-2.1b`는 한국어 인젝션 F1 0.844로 PromptGuard(0.751)·GPT-4o(0.804)를 앞선다. Apache 2.0이고 단일 토큰 출력이라 판정이 빠르다. **하지만 2B 디코더를 노트북 CPU 인라인에 둘 수는 없다**(§1.4).

```
[교사] kanana-safeguard-prompt-2.1b (2.1B, Apache 2.0)
          │  GPU에서 대량 한국어 프롬프트에 의사라벨 부여
          ▼
[학생] A.X-Encoder-base (0.1B, Apache 2.0) 시퀀스 분류
          │  노트북 CPU에서 ~15ms
          ▼
      인라인 게이트웨이에 배포
```

교사와 학생이 **둘 다 Apache 2.0**이라 이 파이프라인 전체가 라이선스상 깨끗하다. 그리고 distillation은 단순 압축이 아니다 — 우리 KoTIP-v2의 **한국어 고유 우회 벡터**(자모분해·초성체·로마자·한자혼용)를 학생에게 추가로 가르치므로, **교사가 못 잡는 것을 학생이 잡는 구간**이 생길 수 있다. 그게 사실인지는 측정 대상이다.

### 3.3 L3 — 학습하지 않는다

`kanana-safeguard-8b`(한국어 유해 F1 0.946)와 `siren-8b`(법적·개인정보 리스크 F1 0.926)는 그대로 쓴다. 솔로가 이길 수 없고, 비동기 감사 라벨링이라 레이턴시 압박도 없다.

### 3.4 기각 목록과 사유

| 대상 | 기각 사유 |
|---|---|
| `klue/roberta-*` | **CC-BY-SA-4.0.** ShareAlike가 학습된 가중치에 전파되는지 법적으로 불확실하다. 제품에 임베드해 배포할 계획이면 피한다. **평가 비교용으로만** |
| ShieldGemma 1/2 | 영어 중심, 2는 이미지 전용, **Gemma 4 기반 후속이 없다** |
| Llama Guard / Prompt Guard | 한국어 공식 미지원 + Llama Community License |
| EXAONE | 자체 라이선스로 상업 제약 가능성 — 확인 전 배제 |
| 자체 유해성 모델 학습 | Kanana 8b(F1 0.946)를 못 이긴다 |

---

## 4. 학습 및 테스트 데이터

### 4.1 라이선스 심사 결과 — 쓸 수 있는 것과 없는 것

**이 절이 이 문서에서 가장 중요하다.** 상용 배포 가능한 온프렘 제품을 목표로 하므로, 라이선스가 데이터 선택을 사실상 결정한다.

#### ✅ 사용 가능

| 데이터 | 라이선스 | 규모 | 용도 |
|---|---|---|---|
| **KDPII** | **CC-BY-4.0** | ~53,778 | **R1 주 학습 데이터.** 한국어 대화체 PII |
| **PolyGuardPrompts / PGMix** | **ODC-BY** | 17개 언어에 **한국어 포함** | **R2 주 학습 데이터** |
| `gretelai/synthetic_pii_finance_multilingual` | **Apache-2.0** | 55.9k | 스키마 참조 + **직접 한국어 현지화** |
| `ai4privacy/pii-masking-openpii-1m` | **CC-BY-4.0** | 1.43M | ⚠️ **한국어 없음.** 다국어 워밍업·스키마 참조만 |
| KoBBQ / SQuARe / KoSBi | **MIT** | — | benign 코퍼스 보강 |
| `deepset/prompt-injections` | Apache-2.0 | 수백 | 시드 프롬프트 |
| **자체 합성 코퍼스** | 자체 | 목표 50k+ | **R1·R2 공통 — 실제로 가장 중요** |

#### ❌ 사용 불가 — 반드시 제외

| 데이터 | 사유 |
|---|---|
| **`smilegate-ai/korean_unsmile`** | **CC-BY-NC-ND-4.0.** NC(비상업) + ND(파생물 금지) 둘 다 걸린다. 학습 모델이 파생물로 해석될 여지가 크다 |
| **AI-Hub 전체** | 제3자 제공 금지, 상업적 이용은 수행기관 별도 협의, 국외 소재 시 NIA 별도 합의. **서면 승인 전까지 파이프라인에 넣지 않는다** |
| **`ai4privacy` 300k/400k/financial-pfi** | 독자 제한 라이선스. 한국어가 있는 `financial-pfi-400k`는 상용 개별 계약 필요 |
| KOLD, LoL 필터링 리스트 | 라이선스 불명 |
| `TTA01/AssurAI` | Restricted |

#### ⚠️ 조건부

| 데이터 | 조건 |
|---|---|
| KLUE-NER, K-MHaS | CC-BY-SA — **평가용으로만.** 학습에 쓰면 SA 전파 리스크 |
| 국립국어원 모두의 말뭉치 | 이용약정에 **기간 제한** 조항. 상용 제품 학습에 부적합 가능성 |
| KoRSET/CAGE, 한-영 Jailbreak 논문 데이터 | 라이선스 미확인 — 저자 문의 필요 |

### 4.2 정정 — 앞선 문서의 오류

기획서 §3.1이 `korean-guardrail-dataset`을 held-out 검증용으로 쓴다고 적었는데, **그 리포는 저장소 레벨 라이선스가 없다**(기본적으로 All rights reserved). 그리고 그 안에 `korean_unsmile`(CC-BY-NC-ND)이 섞여 있다.

→ **리포를 통째로 쓰지 않고, 개별 데이터를 원 출처에서 라이선스를 확인해 가져온다.** 특히 인젝션 파트(raccoonbench-kr 59건 + prompt-injections-benchmark-kr 5,000건)는 원 출처 확인 후에만 쓴다.

그리고 `ai4privacy`에 한국어가 있을 것으로 가정했는데 **오픈 라이선스 세트에는 한국어가 없다.** 한국어는 상용 전용 세트에만 있다. 이 정정으로 **자체 합성 코퍼스의 비중이 훨씬 커진다.**

### 4.3 자체 합성 코퍼스 — 이 프로젝트의 실질 기여

공개 데이터로 커버되지 않는 부분이 크므로 합성이 보조가 아니라 주력이다. 그리고 PII 평가는 오히려 합성이 유리한 면이 있다 — **통제된 비율로 어려운 케이스를 만들 수 있다.**

**PII 코퍼스 구성 (목표 50k+)**

| 유형 | 비율 | 내용 |
|---|---|---|
| 유효 체크섬 PII | 30% | 8종 식별자, 체크섬 통과. **존재하지 않는 생년월일·지역코드 조합**을 써서 실제 개인정보가 아니게 함 |
| **무효 체크섬** | 20% | 형식은 맞지만 체크섬 실패 → L0가 걸러야 함 |
| **형식 유사 비-PII** | 25% | 주문번호 `2026-0801-1234`, 사번 `EMP-900101`, 제품 시리얼, 송장번호 → **오탐 측정의 핵심** |
| 문맥 의존 PII | 15% | 조사 결합 성명, 도로명·지번 혼합 주소, 자연어 표기 번호 |
| 완전 무해 | 10% | PII 없는 일반 업무 문장 |

**절대 원칙**: 실제 개인정보는 어떤 경로로도 저장소에 넣지 않는다. 주민번호는 유효 체크섬을 갖되 `991332`(13월 32일)처럼 **존재할 수 없는 생년월일**을 쓴다. 검증 스크립트([checksum_probe.py](checksum_probe.py))가 이미 이 방식을 쓴다.

**인젝션 코퍼스 (KoTIP-v2)** — 한국어 고유 우회 벡터를 축으로 삼는다. CAGE/KoRSET(ICLR 2026)이 *"직역한 영어 인젝션으로 학습하면 한국 특유의 공격을 놓친다"*를 실증했으므로, 번역 데이터에만 의존하지 않는 것이 근거 있는 선택이다.

| 벡터 | 예 |
|---|---|
| 자모 분해 | `무시해` → `ㅁㅜㅅㅣㅎㅐ` |
| 초성체 | `ㅁㅅㅎ` |
| 로마자 표기 | `musihae` |
| 한자 혼용 | `無視` |
| 조사·띄어쓰기 파괴 | `이 전 지 시 무 시` |
| 존댓말 사회공학 | "죄송하지만 상무님 지시로…", "감사팀 요청입니다" |
| 유니코드 호환자모 U+3130~ / 전각 | — |
| 기존 6종 | base64, homoglyph, emoji_smuggle, unicode_tag, zero_width, jamo_split (`eval/obfuscate.py` 재사용) |

### 4.4 테스트 데이터 — 세 겹

| 겹 | 구성 | 무엇을 재는가 |
|---|---|---|
| **T1 — 템플릿 홀드아웃** | 학습에 쓰이지 않은 템플릿에서 생성 | 패턴 일반화 |
| **T2 — 외부 held-out** | PolyGuardPrompts 한국어, 라이선스 확인된 외부 셋 | 자작 편향 견제 |
| **T3 — 수동 골드셋** | 수백 건을 직접 라벨링한 실제 문서 스타일 | **진짜 KPI.** 합성 성능도 결국 낙관적이다 |

DLP는 recall이 곧 제품 가치이므로 **T3의 recall이 최종 지표**다.

---

## 5. 학습 방법

### 5.1 학습 과제 두 개 — 왜 이것만 하는가

기획서는 "기본 방침은 학습하지 않는 것"이었다. 그 방침은 유지하되, **로컬 실행이라는 제약에서 필연적으로 따라오는 두 개만** 한다. 학습을 위한 학습이 아니라 설계 제약이 요구하는 학습이다.

| # | 과제 | 왜 필요한가 |
|---|---|---|
| **R1** | 한국 PII NER 파인튜닝 | 어떤 공개 모델도 한국 고유 식별자 태그를 갖고 있지 않다. privacy-filter의 9종에도, SDP의 6종에도 법인등록번호가 없다 |
| **R2** | 인젝션 분류기 distillation | **2B 교사를 노트북 인라인에 둘 수 없다.** 0.1B로 줄이지 않으면 로컬 실행 자체가 성립하지 않는다 |

### 5.2 R1 — 한국 PII NER

**태그 셋 (BIO)**

기존 privacy-filter 9종에 **한국 고유 8종을 추가**한다. 이 추가분이 기여의 실체다.

```
기존 상속: PERSON, PHONE, ADDRESS, EMAIL, DATE, URL, ACCOUNT, HANDLE, IP
신규 추가: KR_RRN(주민등록번호)   KR_FRN(외국인등록번호)  KR_PASSPORT(여권)
          KR_DL(운전면허)        KR_BIZNO(사업자등록)    KR_CORPNO(법인등록)
          KR_NHI(건강보험)       KR_BANK(계좌·은행체계)
```

**학습 설정**

| 항목 | 트랙 A (A.X-Encoder) | 트랙 B (privacy-filter) |
|---|---|---|
| 방식 | 풀 파인튜닝 | LoRA r=16, α=16 |
| 대상 모듈 | 전체 | `q/k/v/o_proj`만, **MoE 백본 프리즈** |
| 학습률 | 3e-5 | 5e-4 |
| epochs | 5 (early stopping) | 10 |
| 배치 | 16~32 | 16 |
| max_len | 512 (16k 가능하나 학습은 짧게) | 512 |
| 옵티마이저 | AdamW, warmup 10% | AdamW |
| 예상 시간 | **~0.5~1.5 GPU-h** (4090) | ~3~6 GPU-h |

**커스텀 태그 셋에는 프리즈 인코더 + 헤드 방식을 쓰지 않는다.** 분류 헤드가 랜덤 초기화라 인코더를 얼리면 성능이 크게 떨어진다. 프리즈는 태스크가 사전학습 목적과 가까울 때만 통한다.

**한국어 토큰화 함정 — 반드시 지킬 것**

| 함정 | 대응 |
|---|---|
| 조사가 엔티티에 붙는다 (`김철수에게`) | **조사를 엔티티에서 제외.** `[PERSON]에게`가 되어야 마스킹 출력이 자연스럽다 |
| 서브워드 ↔ 문자 라벨 정렬이 깨진다 | **`return_offsets_mapping=True`로 문자 오프셋을 진실의 원천으로 삼는다.** BIO 인덱스가 아니라 오프셋으로 스팬 복원 |
| 토크나이저마다 NER 성능이 달라진다 | KLUE는 형태소 기반 서브워드, A.X-Encoder·privacy-filter는 BPE 계열. **후보별 실측 비교** |
| entity-level F1만 보면 과소·과대평가 | **character-level recall을 주 지표로**, entity-level F1 병기. PII는 문자 하나만 새도 유출이다 |

### 5.3 R2 — 인젝션 분류기 distillation

**파이프라인**

```
1. 무라벨 한국어 프롬프트 수집 (공개 + 합성 + KoTIP-v2 우회 변형)
2. 교사 kanana-safeguard-prompt-2.1b (GPU) → 의사라벨 + 로짓
   출력: <SAFE> / <UNSAFE-A1>(인젝션) / <UNSAFE-A2>(프롬프트 리킹)
3. 학생 A.X-Encoder-base 시퀀스 분류 학습
   손실 = α·CE(하드라벨) + (1-α)·KL(교사 소프트 분포)
4. KoTIP-v2 우회 벡터로 추가 학습 — 교사가 못 본 부분
5. 캘리브레이션: FPR 목표에 맞춰 임계값 조정
```

| 항목 | 값 |
|---|---|
| 방식 | 풀 파인튜닝 (0.1B) |
| 손실 | CE + KL distillation, temperature 2.0, α=0.5 (튜닝) |
| 학습률 | 3e-5 |
| epochs | 5 |
| 예상 시간 | ~1 GPU-h |

**교사 추론이 실제 비용이다.** 50k 프롬프트에 2.1B 모델로 라벨링하려면 몇 GPU-h가 든다. 단일 토큰만 생성하면 되므로 배치를 크게 잡을 수 있다.

**FPR 캘리브레이션이 핵심이다.** 로컬 게이트웨이에서 과탐은 즉시 우회(사용자가 꺼버림)를 부른다. 로짓 기반 분류라 임계값 조정이 가능한 것이 시퀀스 분류를 택한 이유다 — 교사의 단일 토큰 생성 방식은 이게 어렵다.

### 5.4 학습 환경

RunPod RTX 4090 24GB. 구 프로젝트에서 쓴 경로를 그대로 재사용한다(`scripts/run_gpu.sh` 패턴).

**핵심 판단: 이건 GPU-hours가 아니라 GPU-minutes 문제다.** 0.1B 인코더 토큰 분류는 4090 한 장으로 하루에 수십 번 실험을 돌릴 수 있다. **병목은 컴퓨트가 아니라 데이터 품질과 라벨 스키마 설계**이고, 예산은 거기에 쓴다.

---

## 6. 평가 설계 — 누수 방지

### 6.1 왜 이 절이 필요한가

구 프로젝트의 klue/roberta PII NER은 span F1 0.9845를 냈지만 **row-level random split**이라 누수 가능 상한이었고 헤드라인으로 쓸 수 없었다. 같은 실수를 반복하지 않는다.

그리고 참조 사례인 `FrameByFrame/privacy-filter-korean`의 F1 0.848도 공개된 split이 5/5/90 행 단위로 보인다. **참조 수치조차 그대로 믿지 않는다.**

### 6.2 템플릿 단위 분할

합성 데이터는 템플릿에서 인스턴스를 찍어낸다. 행 단위로 나누면 **같은 템플릿에서 나온 인스턴스가 train과 test 양쪽에** 들어가고, 모델은 일반화가 아니라 템플릿을 외운다.

```
잘못:  인스턴스 생성 → 무작위 90/5/5 분할
올바름: 템플릿 ID를 먼저 90/5/5 분할 → 각 분할에서 인스턴스 생성
```

### 6.3 누수 탐지 절차

| 방법 | 내용 |
|---|---|
| 템플릿 지문 | PII 스팬을 플레이스홀더로 치환·정규화 후 해시. **train∩test = ∅** 확인 |
| n-gram 중복 | PII 마스킹 후 5-gram Jaccard (`eval/check_contamination.py` 재사용) |
| MinHash/LSH | 파라미터화로 정확 해시가 안 잡는 근사 중복 |
| **성능 갭 진단** | **행 단위 F1 ≫ 템플릿 단위 F1이면 누수 확정.** 두 수치를 모두 보고한다 |

마지막 항목이 가장 강력하다. **두 수치의 차이 자체가 결과의 일부**이고, 그걸 공개하는 것이 참조 사례들과의 차별점이다.

### 6.4 보고 형식

```
엔티티      char-recall  char-F1  entity-F1  FPR(양성셋)   n(템플릿)
KR_RRN         0.xxx     0.xxx     0.xxx      0.xxx        xx
KR_BIZNO       ...
...
[베이스라인] 정규식 단독 / vanilla Presidio / privacy-filter base
[누수 진단] 행 단위 F1 vs 템플릿 단위 F1
```

**재현율 옆에 항상 FPR**(평가 원칙 2조). 단일 평균은 보고하지 않는다 — 엔티티마다 난이도가 다르고 평균은 그것을 감춘다.

---

## 7. 로드맵 반영

| 주차 | 학습 관련 작업 |
|---|---|
| **W1** | 합성 코퍼스 생성기 + 태그 스키마 확정. KDPII 실물 확인(태그 셋·RRN 포함 여부) |
| **W2** | — (게이트웨이·정책 집중) |
| **W3** | **R1 트랙 A/B 병행 학습 → 비교 후 선택.** R2 교사 라벨링 + 학생 학습 |
| **W4** | 템플릿 분할·누수 검사, T1~T3 테스트셋 확정 |
| **W5** | 최종 평가, 베이스라인 비교, 누수 진단 수치 공개 |

**W3 게이트 판단 3건**: ① 트랙 A vs B ② R2 학생이 교사 대비 어느 정도 떨어지는지(허용 범위 결정) ③ 추가 학습이 필요한지.

---

## 8. 미결 사항

| # | 항목 | 시점 |
|---|---|---|
| 1 | **KDPII 실물 태그 셋** — 주민번호가 네이티브로 있는가. FrameByFrame이 합성 RRN을 따로 추가한 정황상 없을 가능성 | W1 |
| 2 | **A.X-Encoder-base의 토큰 분류 성능** — 모델 카드에 KLUE-NER 수치가 없다. 직접 벤치마크 필요 | W3 |
| 3 | **privacy-filter MoE 라우터** — 학습시킬지 프리즈할지. 소량 데이터에서 라우터 학습은 expert collapse 위험 | W3 |
| 4 | CC-BY-SA(KLUE-NER, K-MHaS)의 SA가 모델 가중치에 전파되는가 | 법무 검토 |
| 5 | KoRSET/CAGE 라이선스 | 저자 문의 |
| 6 | `team` 모드를 어디까지 설계만 하고 남길 것인가 | W5 |

---

## 9. 참조

**데이터**: [KDPII (Zenodo, CC-BY-4.0)](https://zenodo.org/records/10968609) · [KDPII 논문 (IEEE Access)](https://ieeexplore.ieee.org/document/10681073/) · [PolyGuardPrompts (ODC-BY)](https://huggingface.co/datasets/ToxicityPrompts/PolyGuardPrompts) · [PolyGuard 논문 (arXiv:2504.04377)](https://arxiv.org/abs/2504.04377) · [gretelai synthetic_pii_finance (Apache-2.0)](https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual) · [ai4privacy openpii-1m (CC-BY-4.0)](https://huggingface.co/datasets/ai4privacy/pii-masking-openpii-1m) · [KoBBQ (MIT)](https://github.com/naver-ai/KoBBQ) · [AI-Hub 이용정책](https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105)

**모델**: [skt/A.X-Encoder-base (Apache-2.0, ModernBERT, 16k)](https://huggingface.co/skt/A.X-Encoder-base) · [openai/privacy-filter (Apache-2.0)](https://huggingface.co/openai/privacy-filter) · [transformers 문서](https://github.com/huggingface/transformers/blob/main/docs/source/en/model_doc/openai_privacy_filter.md) · [FrameByFrame/privacy-filter-korean (레시피 참조)](https://huggingface.co/FrameByFrame/privacy-filter-korean) · [kanana-safeguard-prompt-2.1b (Apache-2.0)](https://huggingface.co/kakaocorp/kanana-safeguard-prompt-2.1b)

**방법론**: [템플릿 단위 분할과 누수 (arXiv:2009.04915)](https://arxiv.org/pdf/2009.04915) · [KLUE 논문 — 문자 단위 평가 (arXiv:2105.09680)](https://arxiv.org/pdf/2105.09680) · [CAGE/KoRSET — 문화 적응 레드티밍 (arXiv:2602.20170)](https://arxiv.org/abs/2602.20170) · [LoRA 시퀀스 분류 (HF blog)](https://github.com/huggingface/blog/blob/main/Lora-for-sequence-classification-with-Roberta-Llama-Mistral.md) · [한국어 NER 언어특성 (Cambridge NLE)](https://www.cambridge.org/core/journals/natural-language-engineering/article/korean-named-entity-recognition-based-on-languagespecific-features/FF57D2CB19594A977399B79906292BF7)
