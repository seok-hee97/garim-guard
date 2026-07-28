# SafeAgent-KR — 실제 구동 시스템 전환

> 연구/평가 하네스 → 실제로 돌아가는 에이전트 서비스. 이 문서는 **현재 구현된 것**과
> **남은 로드맵**을 함께 담는다. 설계 근거는 [ARCHITECTURE.md](ARCHITECTURE.md).

핵심 통찰: `run_episode`(src/agent/loop.py)가 **이미 순수 async 함수**라 서비스화는
재작성이 아니라 **배선**이다. `vault.restore`/`contains_raw`/`usage` 도 이미 구현돼 있어
연결만 하면 된다.

---

## 지금 구동되는 것 (이번 증분에서 구현·검증 완료)

```bash
uvicorn src.agent.server:app --port 8000
curl -s localhost:8000/healthz     # {"status":"ok"}
curl -s localhost:8000/readyz      # {"status":"ready","provider":"gemini"}
curl -s -XPOST localhost:8000/v1/agent/run -H 'content-type: application/json' \
     -d '{"input":"고객 010-1234-5678 환불 상태를 조회해 주세요"}'
```

| 영역 | 구현 | 파일 |
|---|---|---|
| **HTTP 서비스** | FastAPI, startup 싱글턴(policy/provider/rail/world/큐/principal), 요청스코프 vault, `/healthz`·`/readyz`·`/v1/agent/run`·`/v1/approvals`·`/v1/audit` | [server.py](../src/agent/server.py) |
| **HITL 승인 큐** | 비가역 도구(환불)는 즉시 실행 아닌 `hitl_pending` → 사람 승인 후 백엔드 실행. `GET/POST /v1/approvals` | [hitl.py](../src/agent/hitl.py) |
| **상태 백엔드 + idempotency** | ServiceWorld: 환불 원장·이메일 outbox·이중환불 방지(idempotency_key). `/v1/audit` 로 관측 | [backend.py](../src/agent/backend.py) |
| **authN/authZ (confused-deputy 차단)** | `X-API-Key`→principal, 수신자/계좌는 담당 고객의 등록값이어야(구조적 바인딩 — taint 아님) | [authz.py](../src/agent/authz.py) |
| **서버 고정 방어** | `tier_a`+`dlp_vault`+`strict_schema` 고정 — **클라이언트가 약화 불가** | server.py `SERVER_DEFENSES` |
| **fail-closed seam** | before_model/before_tool 예외 = 크래시 아닌 안전한 차단. generate 예외 = 우아한 `error` 종료 | [loop.py](../src/agent/loop.py) |
| **provider 복원력** | tenacity 지수백오프+지터(429/5xx/타임아웃만), 벽시계 타임아웃, **async 네이티브**(aio/AsyncOpenAI — 이벤트루프 비블로킹) | [gemini.py](../src/providers/gemini.py)·[openai_provider.py](../src/providers/openai_provider.py)·[_resilience.py](../src/providers/_resilience.py) |
| **벽시계 예산** | `deadline_s`·`per_call_timeout_s` — 느린 도구/모델이 요청 무한점유 방지 | loop.py |
| **구조적 로깅** | JSON 로그 + `episode_id` 상관(contextvar), ★PII 미로깅★ | [log.py](../src/agent/log.py) |
| **DLP 배선** | `vault.restore`(최종 응답만)·egress DLP scan(외부싱크 잔여 원본 PII 차단)·after_tool fail-closed | loop.py·policy.py |
| **Tier A 엄격 스키마** | 미선언 인자 거부 + 정수 암묵 형변환 거부 (gated: 서비스 on / 평가 off → 재현성 보존) | policy.py |
| **테스트** | 66 통과 — 서버 스모크 + **HITL 승인→환불 실행·idempotency·거부·confused-deputy authz 차단** + seam fail-closed + deadline + strict schema | [tests/](../tests/) |

**라이브 검증(2026-07):** 실 gpt-4o-mini 로 `POST /v1/agent/run` → `lookup_customer(phone=<KR_PHONE_1>)`
(DLP 가 모델이 보기 전 전화번호 토큰화), 게이트 통과, `finish=done`. Gemini 는 당시 503
과부하였고 **재시도 소진 후 크래시 없이 `error` 반환**(복원력 실증).

> **정직한 한계(현재 서비스 — 파일럿 전 단계):**
>
> *구현·통합 완료:* **HITL 승인 큐**(비가역 도구는 사람 승인 후 실행, **트랜잭션적** — 백엔드 실패 시 미커밋·재시도) ·
> **상태 백엔드+idempotency**(환불/이메일 이중실행 방지) · **authN/authZ**(X-API-Key **fail-closed 401** + principal↔고객
> 바인딩 → **confused-deputy 구조적 차단**) · **/v1/audit PII 마스킹** · **병렬 HITL 다건 적재(유실 방지)** · **html_escape 배선** ·
> **DLP×TierA 과차단 수정** · **4 seam fail-closed** · **strict_schema 실검증** · **deadline 벽시계** · **egress casefold** ·
> **레일 폴백 표면화** · **env키 노출 제거(파일+git 히스토리)** · **시스템 프롬프트 배선**.
>
> *아직 남은 한계(로드맵):*
> - **실 외부 API 미연결** — ServiceWorld 는 인메모리(환불 원장·이메일 outbox). 실 Stripe/SMTP 연결 지점은 `backend.py` TODO. 실 API 시 `asyncio.to_thread` 만료해도 스레드 미회수 → 자체 소켓 타임아웃 필요. `_refund_seq` check-then-set 도 실 async/워커 도입 시 Lock 필요(현재는 이벤트루프 직렬화로 도달 불가).
> - **cross-customer 스코프** — X-API-Key 는 fail-closed 이나 **데모 principal 은 전-고객 flat 스코프**라 담당 외 고객의 등록 이메일로도 발송 가능. 실운영은 **에피소드↔대상고객 per-ticket 바인딩** 필요(confused-deputy=미등록주소는 이미 차단).
> - **refund authz ≡ egress(demo)** — `authz.account_unverified` 는 `egress.refund_accounts` allowlist 와 동치라 데모에선 egress 가 먼저 차단(구조적 도달 불가). 실운영은 전역 유효계좌 ⊋ 담당계좌 로 분리해야 authz 가 의미.
> - **인메모리 상태·승인 위생** — 큐/백엔드 재시작 소실(실운영 DB/redis + 승인 TTL). 승인자 authN·직무분리(요청자≠승인자) 데모 미강제.
> - **본문(body) 토큰 유지 · lookup authz 미제한 · 대시보드/경보 미구현 · 전체 대화 재개 미구현.**

---

## 레퍼런스 아키텍처

```mermaid
flowchart TB
    U[클라이언트 요청+세션토큰] --> AUTH[authN/authZ · principal 해석]
    AUTH --> RL[레이트리밋+세마포어 · 백프레셔]
    RL --> LOOP

    subgraph startup[앱 기동 1회]
      POL[load_policy · Pydantic 검증]
      PRV[Provider async · tenacity]
      RAIL[ko_agentguard CPU · log-only]
    end
    POL --> LOOP
    PRV --> GEN
    RAIL --> AT

    subgraph req[요청 스코프 · run_episode]
      VAULT[Vault scope=request 신규]
      LOOP[ReAct 4-seam 루프]
    end

    subgraph seams[4 seam · 전부 fail-closed]
      BM[before_model · DLP redact] --> GEN[generate · timeout+retry]
      GEN --> BT[before_tool · ★Tier A★ + egress DLP]
      BT --> AT[after_tool · taint/DLP]
    end
    LOOP --> BM
    BT -->|requires_hitl| HITL[HITL 승인 큐 · checkpoint/resume]
    BT --> TOOL[실도구 어댑터 · timeout·idempotency-key]
    TOOL --> AT --> LOOP
    LOOP -->|종료 시| VAULT
    BM & BT & AT -.emit.-> OBS[구조적 로그·메트릭·트레이스·경보]

    classDef done fill:#1b5e20,color:#fff
    classDef todo fill:#5d4037,color:#fff
    class BM,GEN,BT,AT,VAULT,LOOP,PRV,POL,RAIL,OBS,AUTH,HITL done
    class RL,TOOL todo
```
녹색=구현됨 · 갈색=남은 로드맵(RL=레이트리밋/백프레셔, TOOL=실 외부 API 어댑터).

---

## 남은 로드맵

### 구현 완료 ✅ (이번 통합 증분)
- ~~authN/authZ~~ → **완료**: `X-API-Key`→principal, 게이트에 principal↔고객 바인딩(confused-deputy 구조적 차단).
- ~~HITL 실제 큐~~ → **완료**: `hitl_pending` → `/v1/approvals` 승인 → 백엔드 실행(idempotent). (전체 대화 재개 대신 보류-액션 실행 방식.)
- ~~상태 백엔드 + idempotency~~ → **완료**: ServiceWorld 환불 원장·outbox·이중환불 방지.

### MVP-prod 나머지 (소규모 실파일럿 · 1개월)
1. **실 외부 API 어댑터** — ServiceWorld 인메모리 → 실 Stripe/SMTP 연결(`backend.py` TODO). 실 API 는 자체 소켓 타임아웃 필요.
2. **상태 영속화** — 승인 큐·백엔드를 DB/redis 로(재시작 생존) + 승인 TTL + 승인자 authN·직무분리.
3. **전체 대화 재개** — 현재 HITL 은 보류-액션만 실행. 승인 후 모델이 결과를 보고 대화를 이어가려면 checkpoint/resume + thought_signature 지속성 필요.
4. **환경 오버레이 + 부팅 검증** — `APP_ENV` 기반 `policy.{env}.yaml` deep-merge, prod 는 시크릿 env 주입만, Pydantic `PolicySchema` fail-fast(`dlp.fail==closed`·`hitl.mode!=stub`·`afc==False` 단언).
5. **컨테이너 + CI + lock** — 멀티스테이지 Dockerfile(core/full), `pip-compile --generate-hashes`, GitHub Actions(게이트 봉인 회귀 + presidio 스모크).
6. **재현성 키** — `eval/run.py _done()` dedup 키에 provider/model/rail/dataset_hash 추가, `build_dataset.py` `hash()`→`hashlib`(cross-build 결정성).

### hardened (규모·규제 발생 시)
- 대시보드+경보 4종: `leak_total>0` 즉시 page · 차단율 밴드 · rail 드리프트 · 429율. prometheus_client + Grafana.
- 레일 서빙: R6 PII NER·R7 유해를 추론 사이드카(GPU)로 분리, `after_tool` rail.score 를 `to_thread`. `RemoteRail`(RailScorer 계약 유지, fail-open).
- PII 커버리지: RegexVault 5종 → Presidio 볼트 스왑(여권/면허/사업자 + NER 인라인). **이름(김민준) 등 비정형 PII 는 현재 미커버.**
- 정책 거버넌스·핫리로드(SIGHUP+재검증), 감사로그 해시체인, 병렬 도구 saga 롤백, Tier C 격리, 동시성 세마포어/토큰버킷.

### 오버엔지니어링 경계 (개인/소규모는 하지 말 것)
핫리로드 · GPU 레일 런타임 배선 · 카나리/블루그린 · saga · 감사 서명 · Kubernetes.
**강제(Tier A)가 이미 결정론적으로 봉인**돼 있고 추가 레일은 log-only 라, 서빙 안 해도 안전 성질이 깨지지 않는다.
