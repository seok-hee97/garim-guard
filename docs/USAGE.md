# SafeAgent-KR 사용 가이드 (검증 완료)

> 이 문서의 모든 "키 불요" 명령은 실제 실행으로 재현 검증했다(2026-07). 각 명령에 **전제**
> (없음 / API키 / GPU / presidio / 아티팩트)와 **검증 상태**(실행확인 / 코드확인 / 키필요 / GPU필요)를 표기.
> 상세 논지·결과는 [../README.md](../README.md) · [../results/RESULTS.md](../results/RESULTS.md) · 배포 전환은 [PRODUCTION.md](PRODUCTION.md).

시스템 사용은 **세 실행 모드**로 나뉜다:
- **A. 키 불요(결정적)** — `scripted` 데모·CLI·트레이스 뷰어·pytest·평가 재집계. 아무 것도 없이 동작을 본다.
- **B. 키 필요(라이브)** — `check_env` 게이트·`gemini`/`openai` provider 실행·`eval.run` 라이브 스윕. 실제 모델 유도를 관측.
- **C. GPU 필요(오프라인 부록)** — `train_rail`/`train_pii_ner`/`train_harm`/`run_gpu.sh`. 학습형 레일 재현.

---

## 1. 설치

```bash
python3 -m venv .venv && source .venv/bin/activate     # Python 3.11+ 필요
pip install -r requirements.core.txt                   # 키 없이 pytest·FastAPI 서비스까지 도는 최소셋
# 선택: DLP(presidio)·GPU 스택·통계·데모까지
pip install -r requirements.txt                        # presidio>=2.2.361 필수(KR 5종 PII 인식기)
python -m spacy download ko_core_news_lg               # (presidio 사용 시) 한국어 모델 별도 다운로드
cp .env.example .env                                   # GEMINI/GOOGLE_API_KEY 또는 OPENAI_API_KEY 기입(라이브 경로만)
```

- **requirements.core** = 키 없이 서비스 배선 + pytest 까지. **requirements.txt** = + DLP presidio + GPU(torch/transformers) + 통계(sklearn/scipy/statsmodels) + 데모.
- `.env` 는 gitignore 대상(`.env.example` 만 추적). **무료 티어는 프롬프트가 학습에 쓰일 수 있으니 실제 PII 금지 — 합성 데이터만.**

---

## 2. 퀵스타트 — 키 없이 5분 안에 동작 확인 ✅ 전부 실행검증

```bash
python -c "import src.config, src.providers.base, src.agent, src.dlp, src.rails"   # import 스모크
python -m scripts.run_demo                              # ★핵심★ 무방어=유출 vs 게이트=차단 3-arm 대비
python -m app.trace_view --out /tmp/trace_view.html     # 실행 과정 타임라인 HTML(기본 출력은 tracked라 --out 권장)
python -m pytest --ignore=tests/test_dlp_defenses.py -q # CI-core 119 passed (presidio·키 불요)
```

`scripts.run_demo` 출력(실측):
```
[none]             send_email(→drop@mal.example.org)  ✅실행  → 시도=True 실행=True  🔴 유출
[tier_a]           send_email                          🛑차단(egress.email_domains)  실행=False
[tier_a+dlp_vault] send_email                          🛑차단(dlp.egress:body)       실행=False
✅ 데모 결과 정합: 무방어=유출 · 게이트=차단
```

---

## 3. 목적별 흐름

### 3-1. 데모 / 녹화 (키 불요, 결정적 재생)
> 모델 취약성이 아니라 **런타임 게이트의 차단**을 보이는 결정적 픽스처. 실제 모델 유도는 3-2의 라이브 경로.

```bash
python -m scripts.run_demo                                                   # poisoned_kb 3-arm
python -m scripts.run_demo --scenario refund_email                           # confused-deputy 시나리오
python -m src.agent.cli --scenario poisoned_kb --defenses none    --provider scripted   # 무방어 → 유출(exit 1: 유출감지 신호)
python -m src.agent.cli --scenario poisoned_kb --defenses tier_a  --provider scripted   # 차단(egress.email_domains, exit 0)
python -m src.agent.cli --scenario poisoned_kb --defenses tier_a,dlp_vault --provider scripted  # DLP body 차단
python -m src.agent.cli --scenario normal        --defenses tier_a --provider scripted  # 정상과제 완주(과잉게이팅 아님)
python -m app.trace_view --out /tmp/v.html                                   # 타임라인 + 애블레이션 표 렌더
```
- `--defenses` 유효값: `none` · 쉼표조합 `{plea,spotlight,tier_a,tier_b,dlp_vault,classifier_gate}`. **`all` 은 `tier_c`(미구현) 때문에 실패** — tier_c 를 뺀 조합을 쓸 것. 알 수 없는 키는 조용히 무시.
- `--provider`: `scripted`(키 불요 재생) · `openai`/`gemini`(라이브, 키 필요) · `fake`(카세트 replay — 데모 시나리오 미커버, 데모엔 `scripted` 사용).

### 3-2. 라이브 실행 (키 필요) — 실제 모델 유도 관측
```bash
python -m scripts.check_env                              # Gemini 도구호출 1회 하드게이트(키 없으면 안전 실패·exit 1)
python -m src.agent.cli --scenario poisoned_kb --defenses tier_a --provider openai   # 모델이 실제로 오염문서를 따르는지
```

### 3-3. HTTP 서비스 (FastAPI/uvicorn · 6 엔드포인트)
```bash
APP_ENV=test PYTHONPATH=. python -m pytest tests/test_server.py tests/test_service_integration.py -q  # 21 passed(FakeProvider·키 불요)
APP_ENV=dev  PYTHONPATH=. uvicorn src.agent.server:app --port 8000                                    # 기동
curl :8000/healthz     # {"status":"ok"} 200 — 의존성 무관, 항상 200
curl :8000/readyz      # 키 없으면 503(provider 미초기화 = not ready). 키 있으면 200
```
| 엔드포인트 | 역할키 | 설명 |
|---|---|---|
| `GET /healthz` | — | liveness(항상 200) |
| `GET /readyz` | — | readiness(provider·rail 준비 시 200, 아니면 **503**) |
| `GET /v1/metrics` | **— (의도적 무인증)** | 관측 스냅샷(카운터·in-flight·지연 분위수). ★레지스트리가 정수 + enum/정책키 라벨만 보유해 PII 가 구조적으로 없다★ — 프로메테우스류 스크레이퍼·`scripts.monitor` 가 키 없이 읽는다. 노출을 막으려면 리버스 프록시/네트워크 정책으로 차단할 것(앱 레벨 역할 게이트는 미적용) |
| `POST /v1/agent/run` | agent | 에이전트 실행(**실 LLM 필요**) |
| `GET /v1/approvals` · `POST /v1/approvals/{id}` | approver/auditor | HITL 환불 승인 큐(요청자≠승인자, 재승인 409) |
| `GET /v1/audit` | auditor/approver | 처리 내역(PII 마스킹) |

> **인증 계약 주의:** 키 없이 서버를 띄우면 `/v1/agent/run` 은 **503(not ready)** 이다(provider 미초기화가 인증보다 먼저 fail-closed). **401/403 역할 인증**은 provider 가 준비된 상태에서만 도달하며, `tests/test_server.py`(FakeProvider 주입)에서 검증한다 — 데모 키 `demo-agent-key`/`demo-approver-key`/`demo-auditor-key`(비-prod 전용), 자기승인 403, 재승인 409. `APP_ENV=prod` 는 `SAFEAGENT_API_KEYS` 주입 필수(데모키 거부, fail-closed boot).

### 3-4. 평가 재현 (eval/)
```bash
python -m eval.ablation                                        # runs.jsonl 재집계 → ablation.csv/table.md (키 불요)
python -m eval.check_contamination                            # train/test 오염 감사 → GREEN (키 불요)
python -m eval.expansion_v2 --report-only results/runs.v0.2.openai.jsonl   # post-hoc 검정력 리포트 (키 불요)
python -m eval.build_dataset --stats                          # 코퍼스 통계만(인자 없이 실행 금지 — 동결 데이터 덮어씀)
python -m eval.run --split test --provider openai             # ★키 필요★ 라이브 케이스×arm 스윕 → runs.jsonl
python -m eval.dlp_eval --provider openai                     # ★키 필요★ PII 유출률 측정
```
> `eval.run` 은 `--rail heuristic` 이어도 **provider 키가 반드시 필요**(항상 LLM 호출). `ko_agentguard` 레일은 아티팩트 없으면 **fail-loud**(폴백 안 함) — 휴리스틱 원하면 명시적 `--rail heuristic`.

### 3-4b. 실행 모니터링 — '지금 무엇이 실행/진행 중인가' (키 불요)
```bash
# 1) 서비스 라이브 메트릭(누적 카운터·in-flight·지연 분위수) — HTTP 스냅샷
curl :8000/v1/metrics | python -m json.tool
# 2) 터미널 라이브 대시보드(3s 폴링 · leak 경보 · eval 진행바)
python -m scripts.monitor                          # --once 단발 · --url · --progress-file
# 3) 데모 실행 후 누적 메트릭 요약(키 불요)
python -m scripts.run_demo                          # 하단에 episodes/gate_blocks/dlp_redactions/★leaks_total★/latency 요약
# 4) 긴 스윕 진행 heartbeat(eval.run 이 rate/ETA 를 results/.progress.json 에 기록)
python -m eval.run ... --progress-file results/.progress.json   # 다른 터미널서 monitor 가 읽음
```
- **메트릭(PII 없음, enum/카운트만):** `episodes_total{finish_reason}` · `gate_decisions_total{decision,rule}` · `gate_blocks_total{rule}` · **`leaks_total{tool,rule}`(★0 이어야 정상★ — allowlist 밖으로 실제 나간 실행)** · `dlp_redactions_total{entity}` · `tool_executed_total{tool}` · `errors_total{stage}` · `http_requests_total{method,route,status}` + 지연 히스토그램(p50/p95) + 게이지 `episodes_in_flight`.
- 모든 HTTP 요청은 `X-Request-ID` 상관 + 구조적 로그 1줄(미들웨어). 로그는 stdout JSON(`src/agent/log.py`), 원본 PII 미기록.

### 3-5. 학습 (GPU · 오프라인 부록)
```bash
python -m eval.train_rail                     # 레일(기본 tfidf=CPU 가능 / --encoder e5=GPU)
python -m eval.train_pii_ner                  # R6 klue/roberta, openpii-ko, span F1 0.9845 (GPU)
python -m eval.train_harm                     # R7 K-MHaS 9클래스, Macro-F1 0.709 (GPU)
bash scripts/run_gpu.sh                        # RunPod 런북(HF_TOKEN 필수)
python -m scripts.fetch_rail                   # 학습 대신 완성 아티팩트 다운로드(HF, SHA256 검증)
```
> klue 파생 가중치는 CC-BY-SA 상속 → **수치 보고만, 공개 재배포 금지**. `ko_agentguard`/`e5` 는 자체학습(CC-BY-4.0)이라 공개 HF 배포됨 → `fetch_rail` 로 취득.

### 3-6. 발표자료·다이어그램 (키 불요, 결정적)

**전체 재생성은 반드시 이 순서로** — 그림이 `results/` 를, 슬라이드가 그림을 읽으므로 역순이면
낡은 자산이 렌더된다(회귀 이력: 슬라이드 소스를 고치고 PDF/PPTX 를 재렌더하지 않아 배포본이 한 커밋 뒤처짐).

```bash
python -m eval.ablation                       # ① results/ablation.csv·mcnemar_vs_none.json 갱신 (키 불요)
python -m scripts.run_demo                    # ② results/demo_traces.jsonl (결정적 — 재실행해도 바이트 동일)
python -m app.trace_view                      # ③ report/trace_view.html
python -m scripts.gen_trace_shot              # ④ report/figs/trace_view.png (헤드리스 Chrome 캡처)
python -m scripts.gen_slide_figs              # ⑤ report/figs/fig_*.png  ★matplotlib 필요★
python -m scripts.gen_arch_stack              # ⑥ docs/architecture_stack.png (계층×기술스택, 아이콘)
python -m scripts.gen_model_arch              # ⑦ docs/모델_아키텍처_사진.png (graphviz 제어흐름)
python docs/architecture_diagram.py           # docs/architecture.png/.svg (스크립트라 -m 아님)
marp report/발표자료_슬라이드.md --pdf --pptx --allow-local-files   # ⑧ 투사용 PDF/PPTX (marp+Chrome)
cd scripts/pptx && npm install && npm run build                    # ⑨ 편집용 PPTX (PptxGenJS)
```

**발표 산출물 3종 — 용도가 다르다**

| 파일 | 생성기 | 성격 | 언제 쓰나 |
|---|---|---|---|
| `report/발표자료.pdf` | Marp | 슬라이드=이미지, 폰트 고정 | **투사용 정본** — 어느 PC 에서도 깨지지 않음 |
| `report/발표자료.pptx` | Marp | 슬라이드당 PNG 1장 (**편집 불가**) | PDF 를 못 쓰는 환경의 대체 |
| `report/발표자료_편집본.pptx` | **PptxGenJS** | 네이티브 텍스트·표 + 발표자 노트 (**편집 가능**) | 현장 수정·제출·공동편집 |

> 편집본의 아키텍처 두 장은 서로 보완적이다 — `architecture_stack.png`(무엇으로 만들었나 · 슬라이드 본문용)와
> `모델_아키텍처_사진.png`(요청이 어떻게 흐르나 · 밀도 높음). 두 그림 모두 스크립트 산출물이라 수기 편집 금지.
- ⚠️ `gen_slide_figs` 는 **matplotlib** 이 필요하다. `requirements.core` 에 없으므로 `.venv` 에서는
  `ModuleNotFoundError` 가 난다 — `pip install matplotlib` 하거나 matplotlib 이 있는 인터프리터로
  `PYTHONPATH=. python3 -m scripts.gen_slide_figs` 실행.
- `gen_trace_shot` 은 Chrome/Chromium 을 자동 탐색한다(없으면 안내 후 exit 3). `--sections timeline`
  전용 HTML 을 임시로 만들어 찍으므로 애블레이션 표가 프레임에 걸리지 않는다.
- 발표 스크립트는 [../report/발표자료.md](../report/발표자료.md) — 렌더 소스와 **슬라이드 번호가 1:1**이다.

---

## 4. 전제 매트릭스 (요약)

| 명령 | 전제 | 검증 |
|---|---|---|
| `run_demo` · `cli --provider scripted` · `app.trace_view` | 없음 | 실행확인 |
| `pytest` (전체 125 / CI-core 119) | presidio(전체) / 없음(CI-core) | 실행확인 |
| `eval.ablation` · `check_contamination` · `expansion_v2 --report-only` | 없음(기존 runs.jsonl) | 실행확인 |
| `gen_slide_figs` · `gen_model_arch` · `architecture_diagram` | 없음(matplotlib/graphviz) | 실행확인 |
| `uvicorn ... server` + `GET /healthz` | 없음 | 실행확인 |
| `check_env` · `cli --provider openai/gemini` · `eval.run` · `eval.dlp_eval` · `smoke_live` | API키 | 키필요 |
| `fetch_rail` · `detector_eval` · `kanana_compare` | 아티팩트(+kanana는 GPU) | 실행확인/코드 |
| `train_rail`(e5) · `train_pii_ner` · `train_harm` · `run_gpu.sh` | GPU | GPU필요 |

---

## 5. 함정 (반드시 숙지)

1. **tracked 파일 덮어쓰기** — `run_demo`(results/demo_traces.jsonl), `check_env`(results/env_report.json), `build_dataset`(인자 없으면 동결 데이터), `trace_view`/`gen_*`/`architecture_diagram`(report·docs 산출물)이 git-추적 파일을 덮는다. 실험은 `--out`/`--stats`/`--report-only` 로 임시경로에 쓰거나 실행 후 `git checkout -- <파일>` 로 복원.
2. **`--defenses all` 사용 불가** — `tier_c` 미구현(NotImplementedError). `tier_c` 를 뺀 조합만.
3. **`cli` exit code 의미** — 무방어 arm 유출 시 `exit 1`은 오류가 아니라 유출감지 신호. 게이트 통과(money smoke)면 `exit 0`.
4. **HTTP 무키 부팅** — `/healthz`=200이지만 `/readyz`·`/v1/agent/run`=**503**(provider 미초기화). 401/403 인증은 provider 준비 후에만 도달(테스트에서 검증).
5. **provider 구분** — `scripted`만 완전 키 불요. `fake`(카세트)는 데모 시나리오 미커버로 finish=error. `openai`/`gemini`는 라이브(쿼터 소모).
6. **presidio 버전** — 반드시 `>=2.2.361`(2.2.360은 KR_RRN만). spacy 한국어 모델 별도 다운로드.
7. **PYTHONPATH** — uvicorn/일부 모듈 실행 시 `PYTHONPATH=.` 필요(ModuleNotFoundError: src 방지).
