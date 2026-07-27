# SafeAgent-KR

> NIPA-Google 프로젝트 · 2026-07-13 ~ 2026-07-31 · Python · Google 모델 우선 · 개인(솔로)
> 상세 기획·설계: **[project_plan.md](project_plan.md)**
>
> **명명 규약** — `SafeAgent-KR` = 프로젝트/런타임 · `KoTIP-Bench` = 구축 평가셋 · `Ko-AgentGuard` = 학습 레일 모델

---

## 프로젝트 주제 (제출용)

**프로젝트 명칭**
**SafeAgent-KR — 한국어 도구 사용 AI 에이전트 안전 런타임**
*(도구 채널 인젝션·개인정보 유출 평가셋 구축과, 단일 발화 가드레일의 구조적 사각지대 측정)*

**분야**
자연어처리(NLP) · LLM 안전(Safety AI) / 가드레일 · 개인정보 비식별화(DLP/PII) · AI 에이전트(도구 사용·함수 호출) · 데이터셋 구축 · 경량 탐지 모델 학습

**프로젝트 설명**

도구를 호출하는 한국어 AI 에이전트(ReAct 루프 + 5개 mock 도구)를 `before_model / after_model / before_tool / after_tool` 4개 seam으로 감싼 **안전 런타임**을 구현하고, 그 위에서 두 가지를 **측정으로 증명**한다.

**① 채널 조건부 탐지 격차** — 동일한 인젝션 페이로드를 (a) 사용자 발화 (b) 도구 반환값으로 제시했을 때, 한국어 공개 가드 모델은 (a)에서 동작하고 (b)에서 실패한다. 성능 문제가 아니라 **단일 발화를 채점하도록 설계된 모델이 도구 경계 너머를 볼 수 없다는 설계 귀결**이며, 사전 등록한 **예측**으로 검정한다.

**② 분류기 계열 전체의 붕괴** — 난독화를 적용하면 **우리가 만든 레일도 함께 무너지지만** 결정적 정책 게이트(Tier A)의 t-ASR은 불변이다.

결론은 "우리 분류기가 더 낫다"가 아니라 **"분류기는 이 채널에서 강제 수단이 될 수 없으므로 로그 전용으로 강등하고, 강제는 도구별 결정적 게이트가 담당해야 한다"**는 불가능성 논증이다. 여기에 홉 간 일관된 **요청 스코프 가역 PII 볼트**를 결합하고, 발견한 **Presidio 한국어 인식기 결함 2건**을 정량 보고한다.

**핵심 산출물** — 방어층별 애블레이션 표(McNemar 검정) · 채널 조건부 격차 2×2 표 · KoTIP-Bench 평가셋 · Presidio 결함 정량화 · 라이브 데모 · 결과보고서

---

## 구성요소별 (공개 학습셋 × 평가셋 × 지표)

**공개 평가셋 11/11 · 공개 학습셋 3/11.** 강제 계층(Tier A/B/C)에 학습셋이 없는 것은 결함이 아니라 논지 그 자체다 — *학습된 것은 우회 가능하고 결정된 것은 우회 불가능하다*가 주장이므로 거기에 학습을 붙이면 자기모순이 된다.

| 계층 | 구성요소 | 공개 학습 데이터 | 평가 |
|---|---|---|---|
| L0 기판 | R1 ReAct 코어 + 5 도구 + 4 seam | 없음 — 합성 | 자체 benign 100건 |
| **L1 강제** *(결정적)* | R2 Tier A 게이트 · R3 Tier B taint · R4 Tier C | **없음 — 논지 그 자체** | MAPS ko 400건 (CC-BY-4.0) |
| **L2 탐지** *(학습형)* | R5 인젝션 레일 | **Nemotron 간접인젝션 1,272행** (CC-BY-4.0) | Nemotron 홀드아웃 + MAPS + 네이티브 골드 40 |
| | R6 비정형 PII | **openpii-1.5m `ko`** (CC-BY-4.0) | 템플릿 분리 test + KLUE NER |
| | R7 유해성 레일 | **K-MHaS 109,692행** (CC-BY-SA-4.0) | K-MHaS test 21,939 |
| L3 DLP | R8 Presidio-ko + 결함실험 · R9 PIIVault | 없음 — 합성이 유일한 선택 | 자체 Faker `ko_KR` 골드셋 |
| L4 정직성 *(평가전용)* | R10 과차단 프로브 · R11 적대적 강건성 | 없음 — 평가 전용 | XSTest_ko 450행 |

> 사용 자산 8종 중 **7종이 파생물 재배포 가능**. **NC·ND·게이트 데이터셋 0건** → 가공 분할과 자체 골드셋을 산출물과 함께 배포할 수 있다. 전체 라이선스 검증표는 [project_plan.md §6.1](project_plan.md).

---

## 아키텍처

> 상세 설명 — **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** (신뢰 경계 · 4 seam · 방어 3계층 · Vault 수명 · 측정 설계)

```
사용자 요청(신뢰)
  → [P-LLM 계획 FROZEN]                                    ← Tier C
  → ╔ before_model ╗ DLP 비식별 · spotlight · 분류기[LOG-ONLY]
  → provider.generate(messages, cfg)  ★모든 functionCall part 순회★
  → ╔ after_model ╗  per_hop (중간 홉 출력도 검사)
  → ╔ before_tool ╗ ★진짜 게이트★ 위험티어·allowlist·인자검증·taint·HITL  ← Tier A+B
  → [도구 실행 — mock]
  → ╔ after_tool ╗ Q-LLM 격리 · 분류기 재스캔[LOG-ONLY] · DLP 토큰화 · Tainted 래핑
  → 반복 (tool_calls 없으면 종료)
→ 최종 응답 + 트레이스 JSONL (attempted / executed / gate_rule / taint_sources)
```

**설계 규율:** 게이트는 **문자열 내용을 읽지 않는다.** 페이로드가 한국어든 이모지든 base64든 판정이 동일하다 — 난독화 하에서 t-ASR이 불변인 이유이자 논지의 구조적 근거다.

---

## 실행

```bash
# 설치 (D0 는 core 만 — presidio/torch 는 쓰는 날로 격리)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.core.txt
cp .env.example .env    # GOOGLE_API_KEY

# 환경·데이터 점검
python -m scripts.check_env        # 키·모델ID·RPM 실측
python -m eval.data_spike          # 6개 로더 관통 → data/manifest.json

# ★ money smoke — 프로젝트의 핵심 증명 (CLI)
python -m src.agent.cli --scenario poisoned_kb --defenses tier_a
#   기대: 분류기 전부 OFF, 오염 KB → send_email(drop@mal.example.org) 시도 →
#         gate_decision=BLOCK, gate_rule=egress.email_domains, executed=false

# ★ 실제 구동 서비스 (HTTP 런타임) — 서버 고정 방어(tier_a+dlp_vault+strict)
uvicorn src.agent.server:app --port 8000
curl -s localhost:8000/healthz                         # {"status":"ok"}
curl -s localhost:8000/readyz                          # provider 준비 여부
curl -s -XPOST localhost:8000/v1/agent/run \
     -H 'content-type: application/json' \
     -d '{"input":"고객 010-1234-5678 환불 상태를 조회해 주세요"}'

# 평가
pytest                             # 50 통과 (.venv, pytest-asyncio+presidio)
python eval/ablation.py            # → results/ablation.csv (McNemar p, Wilson CI)
python eval/kanana_compare.py      # 채널 조건부 2×2
open report/trace_view.html        # 정적 트레이스 데모 뷰어
```

> 프로덕션 전환 로드맵(레퍼런스 아키텍처·설정·단계별)은 [docs/PRODUCTION.md](docs/PRODUCTION.md) 참조.

---

## 상태 (2026-07-28)

| 항목 | 상태 |
|---|---|
| 런타임 코어 (4-seam ReAct 루프 + Tier A 게이트 + DLP) | ✅ 구현·테스트 |
| 평가 하네스 (KoTIP-Bench + 애블레이션 McNemar/Wilson) | ✅ 구현, 헤드라인 실측 완료 |
| 학습 (R6 PII NER·R7 유해·레일) | ✅ GPU 산출(오프라인, 부록) |
| **실행 서비스 (FastAPI HTTP 런타임)** | ✅ 구동 가능 — 서버고정 Tier A+DLP+strict, 파일럿 전 단계([docs/PRODUCTION.md](docs/PRODUCTION.md)) |
| 외부 데이터셋 실재 검증 | ✅ 완료 (라이선스·행수 실측, [results/RESULTS.md](results/RESULTS.md)) |
| 유닛/통합 테스트 | ✅ **50 통과** (.venv) |

주요 설정: [config/policy.yaml](config/policy.yaml) · 의존성: [requirements.txt](requirements.txt) · 환경변수: [.env.example](.env.example)
