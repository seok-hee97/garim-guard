# SafeAgent-KR

> **한국어 도구 사용 AI 에이전트 안전 런타임** — 도구 채널 인젝션·개인정보 유출을 막고, 강제를 결정적 게이트로 이관.
> NIPA-Google 프로젝트 · 2026-07 · Python · 개인(솔로)
>
> **명명 규약** — `SafeAgent-KR` = 프로젝트/런타임 · `KoTIP-Bench` = 구축 평가셋 · `Ko-AgentGuard` = 학습 레일 모델
> 상세: [docs/USAGE.md](docs/USAGE.md)(사용 가이드·검증됨) · [project_plan.md](project_plan.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/PRODUCTION.md](docs/PRODUCTION.md) · [results/RESULTS.md](results/RESULTS.md)

---

## 1. 프로젝트 개요

도구를 호출하는 한국어 AI 에이전트(ReAct 루프 + 5개 도구)를 `before_model / after_model / before_tool / after_tool` **4개 seam**으로 감싼 안전 런타임이다. 두 가지를 **측정으로 증명**하고, 그 논지를 **실제 구동 가능한 HTTP 서비스**로 구현했다.

- **① 채널 조건부 탐지 격차** — 같은 인젝션을 (a) 사용자 발화로 직접 채점하면 recall 1.0, (b1) `tool` 턴이 포함된 native 대화 템플릿으로 채점하면 0.0, (b2) 도구 반환 문자열을 `after_tool`에서 직접 채점하면 다시 1.0이었다(Kanana, n=10). 즉 **모델 자체보다 어댑터 배선에 따라 탐지 성패가 달라진다.** 실제 런타임은 (b2) 배선이다.
- **② 강제는 분류기가 아니라 게이트에** — 난독화(base64)로 CPU 레일은 F1 0.99→0.0 붕괴하지만, **결정적 정책 게이트(Tier A)의 t-ASR은 불변**이다. 게이트는 **공격 텍스트의 의미를 분류하지 않는다** — 도구 identity·구조화 인자·정책 테이블로만 판정하기 때문.

> **결론:** **분류기 단독으로는 이 채널에서 하드 보장을 제공할 수 없으므로**(온디바이스든 아니든) log-only 텔레메트리로 두고, **강제의 최종 권한은 도구별 결정적 게이트**가 가진다. 여기에 요청 스코프 가역 PII 볼트를 결합한다. *(단 강한 한국어 모델 Kanana는 올바른 문자열 채널에서 recall 1.0·난독화에도 ≥0.9 — "분류기 전부 무효"로 일반화하지 않는다. 분류기 강제 실험은 한국어를 못 보는 영어 CPU 레일만 측정됨.)*

**핵심 결과** (실측, `results/RESULTS.md`): Tier A 게이트가 **관측된 모든 인젝션 실행을 차단**(17% → 0%). ⚠️ v0.1 행 단위 McNemar **p=0.002는 탐색적** — 60건이 2시나리오×5스타일×6난독화라 난독화 변형이 상관됨. 군집(시나리오×스타일 10개) 단위 보수적 재분석은 p=0.125(미유의)였다. **과업 시나리오를 2→12로 늘린 post-hoc 민감도 분석 v0.2**(`eval/expansion_v2.py`)에서, 보수적 시나리오 단위(n=12) McNemar **p=0.031**(무방어 6/6 성공 시나리오를 Tier A가 전부 차단, discordant 6:0). ⚠️ 단 none 대비 4-arm 다중비교라 Bonferroni α/4=0.0125 보정 시 **미유의**이고 post-hoc 이므로, 방어 가능한 주장은 "**구조적으로 모든 성공 시나리오 차단(방향 일관) + 보정 전 p=0.031**"이다. theater 3종은 v0.1·v0.2 모두 p=1.0.

---

## 2. 두 개의 얼굴 — 연구 하네스 + 실행 서비스

| | 연구/평가 하네스 | 실행 서비스 (HTTP) |
|---|---|---|
| 목적 | 논지를 애블레이션으로 측정 | 논지를 실제 에이전트로 구동 |
| 진입점 | `./.venv/bin/python -m eval.run` · `./.venv/bin/python -m src.agent.cli` | `./.venv/bin/python -m uvicorn src.agent.server:app` |
| 세계 | mock `ToolWorld`(부작용 없음, 결정적) | `ServiceWorld`(환불 원장·이메일 outbox·idempotency) |
| 방어 | 애블레이션 축(none/tier_a/…) | 고정 `tier_a+dlp_vault+strict_schema` + authz |
| 인가 | 없음(중립 baseline) | `X-API-Key`→principal, HITL 승인 큐 |

두 경로가 **같은 `run_episode`** 를 공유한다 — 서비스는 재작성이 아니라 배선이다.

---

## 3. 아키텍처

```
요청 (+X-API-Key → principal)                         ← authN/authZ
  → ╔ before_model ╗ DLP 비식별 · plea/spotlight · 분류기[LOG-ONLY]
  → provider.generate  ★모든 functionCall part 순회 · async · 재시도★
  → ╔ after_model  ╗ pass-through (의도적 no-op; provider 자체 안전필터에 위임 — 분류기 강제 아님)
  → ╔ before_tool  ╗ ★결정적 게이트★                   ← Tier A + authz + HITL
        위험티어 · allowlist · 인자 스키마 · egress DLP scan · principal 바인딩
        └ 비가역 도구 → HITL 승인 큐(hitl_pending)
  → 도구 실행 (ServiceWorld: 환불 원장 · 이메일 outbox · idempotency)
  → ╔ after_tool  ╗ 분류기 재스캔[LOG-ONLY] · DLP 토큰화
  → 반복 → 최종 응답 + 트레이스(attempted/executed/gate_rule)
```

**설계 규율:** 게이트는 **공격 텍스트의 의미를 분류하지 않는다** — 도구 identity·구조화 인자·정책 allowlist 로만 판정한다(형식 검증·도메인 대조·egress DLP 스캔에서 문자열을 보긴 하지만, '이 문장이 공격인가'를 판단하지 않는다). 그래서 페이로드가 한국어든 이모지든 base64든 판정이 같다(난독화 불변). 4 seam은 전부 **fail-closed**(예외 시 통과가 아니라 안전한 차단).

상세: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · 프로덕션 전환: [docs/PRODUCTION.md](docs/PRODUCTION.md)

---

## 4. 설치

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.core.txt          # 서비스+평가 코어 (presidio/torch 제외)
# DLP Presidio·GPU 레일까지: pip install -r requirements.txt

cp .env.example .env                           # GEMINI_API_KEY 또는 OPENAI_API_KEY 기입
python -m scripts.check_env                    # 키·모델·RPM 점검

# ★평가 레일 아티팩트(clean clone 필수)★ — ko_agentguard 는 대용량 바이너리(git 위생)라 저장소에 없다.
# (라이선스 제약이 아니라 용량 문제 — CC-BY-4.0 데이터로 자체학습해 공개 HF 에 재배포돼 있다.)
# 평가(eval.run)는 아티팩트가 없으면 fail-loud 하므로(잘못된 레일명 기록 방지), 아래로 받는다:
python -m scripts.fetch_rail                    # HF cycloevan/safeagent-kr → train/artifacts/ (체크섬 검증)
#   sha256(ko-agentguard-lr.joblib)=10cca996f7f193ffcd633dc8cb98e07c4a330db0cb4424400952c4838dadb2c8
#   (없이 실행하려면 명시적으로 --rail heuristic — 결과 actual_rail 에 heuristic 으로 기록됨)
```

> 요구: Python 3.11+. 키 없이도 **전체 테스트, 브라우저 UI의 scripted 모드, `run_demo`, CLI의 scripted 모드**는 동작한다. Gemini/OpenAI live 모드와 기본 HTTP 서비스는 해당 API 키가 필요하다.

---

## 5. 실행 방법

### 5-0. 전체 Agent Workflow 브라우저 프로그램

입력부터 모델 턴, 도구 원본 결과(합성 scripted 한정), DLP 처리 후 모델 전달값,
LOG-ONLY 레일 점수, `before_tool` 게이트 판정, 실행/차단, 최종 답변과 서비스
백엔드 감사 상태를 한 화면에서 확인한다.

```bash
./.venv/bin/python -m uvicorn app.ui:app --host 127.0.0.1 --port 8501
# 브라우저: http://127.0.0.1:8501
# 녹화용 자동 실행: http://127.0.0.1:8501/?autorun=1
```

실행한 터미널을 유지한 상태에서 브라우저로 접속한다. 정상 종료는 서버 터미널에서
`Ctrl+C`를 한 번 누른다.

```bash
# 포트가 이미 사용 중이거나 서버 터미널을 잃어버린 경우 PID 확인
lsof -nP -iTCP:8501 -sTCP:LISTEN

# 위 명령에서 확인한 PID만 정상 종료 요청
kill <PID>

# 종료 확인 — 출력이 없으면 종료 완료
lsof -nP -iTCP:8501 -sTCP:LISTEN
```

`kill -9`는 정상 종료가 반복해서 실패할 때만 마지막 수단으로 사용한다. 프로젝트의
`./.venv/bin/uvicorn` 실행 파일은 다른 경로에서 만들어진 오래된 shebang일 수 있으므로,
문서처럼 반드시 `./.venv/bin/python -m uvicorn ...` 형태로 실행한다.

- `scripted`: API 키 없이 고정된 도구 호출을 실제 런타임에 재생한다. **모델 추론 증거가 아니다.**
- `gemini` / `openai`: `.env` 키를 사용해 실제 모델의 도구 선택을 관측한다.
- `3개 Arm 비교`: 같은 scripted 호출을 `none / tier_a / tier_a+dlp_vault`에 재생한다.
- 학습 레일이 `heuristic`으로 폴백하면 화면에 경고한다. 발표 실행은 프로젝트
  `./.venv/bin/python`을 사용한다.

### 5-1. 핵심 증명 — money smoke

**★키 불요 데모(권장)★** — 같은 공격을 arm 3종에 결정적 재생 + 실행 과정 타임라인:
```bash
./.venv/bin/python -m scripts.run_demo          # none=🔴유출 / tier_a=🛑차단 / tier_a+dlp_vault=🛑차단(body)
./.venv/bin/python -m app.trace_view            # → report/trace_view.html (seam 흐름·차단지점)
./.venv/bin/python -m scripts.run_demo --verbose  # 턴별 모델입력·도구결과·DLP·레일·게이트 전체 출력
```

**CLI 단건**(scripted=키 불요 재생 · openai/gemini=라이브 유도):
```bash
./.venv/bin/python -m src.agent.cli --scenario poisoned_kb --defenses none  --provider scripted  # 🔴 유출
./.venv/bin/python -m src.agent.cli --scenario poisoned_kb --defenses tier_a --provider scripted  # 🛑 차단
./.venv/bin/python -m src.agent.cli --scenario poisoned_kb --defenses tier_a,dlp_vault \
  --provider scripted --verbose  # 전체 Agent Workflow
#   기대(tier_a): gate_decision=BLOCK, gate_rule=egress.email_domains, executed=false
./.venv/bin/python -m src.agent.cli --scenario poisoned_kb --defenses tier_a --provider openai     # 실 모델 유도
```

> 키 없이 **assert 로 고정**된 증명: `pytest tests/test_gate.py -k money_smoke` · `pytest tests/test_demo.py`
> (FakeProvider.scripted로 게이트 차단을 재현 — 모델 취약성이 아니라 게이트 동작을 검증).

### 5-2. 실제 구동 서비스 (HTTP 런타임)

```bash
./.venv/bin/python -m uvicorn src.agent.server:app --host 127.0.0.1 --port 8000
```

정상 종료는 서버 터미널에서 `Ctrl+C`를 한 번 누른다. 서버 터미널을 잃어버렸다면
`lsof -nP -iTCP:8000 -sTCP:LISTEN`으로 PID를 확인한 뒤 `kill <PID>`로 종료한다.
종료 확인은 같은 `lsof` 명령을 다시 실행해 출력이 없는지 확인한다.

| 엔드포인트 | 설명 |
|---|---|
| `GET /healthz` | liveness (의존성 무관 200) |
| `GET /readyz` | readiness (provider·rail 준비 여부) |
| `GET /v1/metrics` | 관측 스냅샷 — 누적 카운터·in-flight·지연 분위수. **의도적 무인증**(스크레이퍼/모니터용, 응답에 PII 없음: 정수 + enum/정책키 라벨만) |
| `POST /v1/agent/run` | 에이전트 실행 (**agent 역할 키 필수**, 없으면 401·역할 불일치 403) |
| `GET /v1/approvals` | 대기 중 HITL 승인 목록 (PII 마스킹) — **approver/auditor** |
| `POST /v1/approvals/{id}` | 승인/거부 → 백엔드 실행(idempotent) — **approver, 요청자≠승인자** |
| `GET /v1/audit` | 처리 내역 관측 (PII 마스킹) — **auditor/approver** |

```bash
KEY='X-API-Key: demo-agent-key'      # ★fail-closed: 키 없으면 401★

curl -s localhost:8000/healthz                                   # {"status":"ok"}
curl -s localhost:8000/readyz                                    # provider 준비 여부

# 정상 조회 (PII는 모델이 보기 전 토큰화됨). ★다중 고객 담당 키는 customer_id 필수(대화-고객 바인딩)★
curl -s -XPOST localhost:8000/v1/agent/run -H "$KEY" -H 'content-type: application/json' \
     -d '{"input":"010-1234-5678 고객 환불 상태를 조회해 주세요","customer_id":"cust_00042"}'
```

**HITL 환불 승인 흐름 (비가역 도구는 사람 승인 후 실행):**

```bash
# 1) 환불 요청 → 즉시 실행 안 됨. status=hitl_pending + pending_approval(id) 반환
curl -s -XPOST localhost:8000/v1/agent/run -H "$KEY" -H 'content-type: application/json' \
     -d '{"input":"이 고객 환불 처리해 주세요","customer_id":"cust_00042"}'
#    → {"status":"hitl_pending","pending_approval":"appr_00001", ...}

# ★직무분리: 승인=demo-approver-key, 감사=demo-auditor-key (agent 키로는 자기승인 403)★
# 2) 승인자가 대기 목록 확인 (계좌·사유 PII 마스킹)
curl -s localhost:8000/v1/approvals -H 'X-API-Key: demo-approver-key'

# 3) 승인자 승인 → 백엔드에 실제 환불 실행 (거부는 {"decision":"deny"}). approver 는 토큰에서 결정
curl -s -XPOST localhost:8000/v1/approvals/appr_00001 \
     -H 'X-API-Key: demo-approver-key' -H 'content-type: application/json' -d '{"decision":"approve"}'

# 4) 감사자가 처리 내역 확인 (재승인은 409, 이중환불 없음)
curl -s localhost:8000/v1/audit -H 'X-API-Key: demo-auditor-key'
```

### 5-3. 평가·재현

```bash
./.venv/bin/python -m pytest -q                         # 129 통과
./.venv/bin/python -m eval.run --split test --provider openai   # 애블레이션 스윕 → results/runs.jsonl
./.venv/bin/python -m eval.ablation                    # → results/ablation.csv · mcnemar_vs_none.json
./.venv/bin/python -m eval.kanana_compare              # 채널 조건부 격차 2×2 (GPU)
open report/trace_view.html         # 정적 트레이스 뷰어
```

### 5-4. 컨테이너

```bash
docker build -t safeagent-kr .
# 빠른 데모(데모 키로 부팅 — APP_ENV 미설정):
docker run -p 8000:8000 -e GEMINI_API_KEY=... safeagent-kr
# 프로덕션(데모 키 거부 — 주입 자격증명 필수):
docker run -p 8000:8000 -e GEMINI_API_KEY=... -e APP_ENV=prod \
     -e SAFEAGENT_API_KEYS='{"<key>":{"id":"agent:acme","role":"agent","customer_ids":["cust_00042"]}}' safeagent-kr
```

---

## 6. 구현된 서비스 기능

| 기능 | 내용 | 파일 |
|---|---|---|
| **결정적 게이트 (Tier A)** | 위험티어·allowlist·엄격 스키마·egress DLP scan. 문자열 미판독 | [policy.py](src/agent/policy.py) |
| **HITL 승인 큐** | 비가역 도구는 `hitl_pending` → 사람 승인 후 실행. **트랜잭션적**(백엔드 실패 시 재시도) | [hitl.py](src/agent/hitl.py) |
| **상태 백엔드 + idempotency** | 환불 원장·이메일 outbox·이중실행 방지 | [backend.py](src/agent/backend.py) |
| **authN/authZ** | `X-API-Key`→principal, 수신자/계좌는 등록 고객값이어야(미등록 주소 차단). `customer_id` 바인딩 시 **한 고객으로 스코프 한정 → 타-고객 confused-deputy 구조적 차단** | [authz.py](src/agent/authz.py) |
| **DLP 볼트** | 요청 스코프 가역 토큰화(5종), 모델·비인가 콘텐츠 필드에는 토큰만 노출. 승인된 라우팅 인자(`to`/`account`)는 실행 경계에서 복원 | [dlp/vault.py](src/dlp/vault.py) |
| **provider 복원력** | async 네이티브 + tenacity 재시도(429/5xx) + 벽시계 타임아웃 | [_resilience.py](src/providers/_resilience.py) |
| **fail-closed 4 seam · 구조적 로깅(PII 미로깅) · deadline 예산** | | [loop.py](src/agent/loop.py)·[log.py](src/agent/log.py) |
| **실행 모니터링** | in-process 메트릭(카운터/히스토그램/게이지·PII 없음) + `GET /v1/metrics` 라이브 스냅샷 + 요청 로깅 미들웨어(X-Request-ID) + `scripts.monitor` 터미널 대시보드 + eval 진행 heartbeat. 사용법 [docs/USAGE.md §3-6](docs/USAGE.md) | [metrics.py](src/agent/metrics.py)·[monitor.py](scripts/monitor.py) |
| **★유출 불변식 계측(`leaks_total`)★** | 게이트가 *막은 것*(`gate_blocks_total`)과 별개로, **실제로 allowlist 밖으로 나간 실행**을 사후 판정해 센다. 무방어 arm 에서만 오르는 게 정상이고 Tier A 가 켜진 채 오르면 게이트 회귀 → 모니터가 빨강 경보 | [loop.py](src/agent/loop.py)·[policy.py](src/agent/policy.py) |

---

## 7. 상태 (2026-07-29)

| 항목 | 상태 |
|---|---|
| 런타임 코어 (4-seam ReAct + Tier A + DLP) | ✅ 구현·테스트 |
| 평가 하네스 (KoTIP-Bench + 애블레이션 McNemar/Wilson) | ✅ 헤드라인 실측 완료 |
| **실행 서비스** (FastAPI + HITL + authz + 상태 백엔드) | ✅ 구동 가능, 파일럿 전 단계 |
| **실행 모니터링** (메트릭·`/v1/metrics`·`scripts.monitor`·`leaks_total`·heartbeat) | ✅ 구현·테스트 |
| 학습 (R6 PII NER·R7 유해·레일) | ✅ GPU 산출(오프라인 부록) |
| 유닛/통합 테스트 | ✅ **129 통과** (.venv; CI-core 123, presidio 6) |
| 발표 자산 | ✅ **단일 소스** — [gen_pptx.mjs](scripts/pptx/gen_pptx.mjs) 하나가 `발표자료.pptx`(편집 가능·정본) + `발표자료.md`(스크립트) 를 만들고, `발표자료.pdf` 는 [export_deck_pdf](scripts/export_deck_pdf.py)(Keynote)로 파생. 수기 캡처·수기 수치 없음 ([USAGE §3-8](docs/USAGE.md)) |

**남은 로드맵** ([docs/PRODUCTION.md](docs/PRODUCTION.md)): 실 외부 API 어댑터(Stripe/SMTP), 상태 영속화(DB/redis), per-ticket 고객 스코프, Prometheus 노출 형식 + Grafana 대시보드 + 외부 page 경보.

**정직성 표기:** 헤드라인은 런타임 배선+근거 있는 3가지(Tier A·Kanana 채널갭·RegexVault DLP)만 주장. R6/R7/R4/ShieldGemma는 오프라인 부록. R10/R11은 데이터셋 매핑만(미실행). 사전등록 예측 P1b는 반증되어 정직 보고.

---

## 8. 라이선스

코드 MIT([LICENSE](LICENSE)) · 데이터/모델 라이선스는 [DATA_CARD.md](DATA_CARD.md). KoTIP-Bench 등 자체 산출물 CC-BY-4.0.
