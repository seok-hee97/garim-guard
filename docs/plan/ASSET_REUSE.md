# 자산 처분 맵 — 구 SafeAgent-KR → 가림 Gateway

> 2026-07-31 작성 · 구 프로젝트 코드 자산의 처분 판단
> 기준 커밋: `74e1ae7` (구 방향의 마지막 완전 상태) · 문서 삭제 커밋: `0a0a91a`
>
> 관련: [기획서 §1.5](기획서.md#15-구-프로젝트safeagent-kr-대비-변경점) · [ARCHITECTURE.md](ARCHITECTURE.md) · [ROADMAP.md](ROADMAP.md)

---

## 처분 기준

| 판정 | 뜻 |
|---|---|
| **KEEP** | 거의 그대로 새 구조에 옮긴다 |
| **ADAPT** | 개념·코드를 가져오되 새 맥락에 맞게 고친다 |
| **REFERENCE** | 코드를 옮기지는 않지만 결과·설계를 인용한다 |
| **DROP** | 새 방향에서 쓰지 않는다. 구현 착수 시 제거 |

**주의**: 이 문서의 DROP은 *구현 단계에서* 제거한다는 뜻이다. 현재 저장소에는 아직 남아 있다. 지금 지우면 `eval/run.py` 등이 깨지고, 그것은 문서 작업 범위 밖이다.

---

## 1. DLP — 축 A의 뼈대 (재사용률 최고)

| 경로 | 판정 | 새 위치 / 사유 |
|---|---|---|
| [src/dlp/vault.py](../../src/dlp/vault.py) | **KEEP** | `RegexVault`가 요청 스코프 `redact`/`rehydrate`/`restore`와 값 단위 coreference(같은 원본값 → 같은 토큰)를 이미 구현했다. **가역 마스킹 볼트의 코어**로 그대로 쓴다. 겹치는 스팬을 긴 것 우선으로 해소하는 로직도 유지 |
| [src/dlp/recognizers_ko.py](../../src/dlp/recognizers_ko.py) | **ADAPT** | 현재 5종(EMAIL, KR_RRN, KR_CARD, KR_PHONE, KR_ACCOUNT)의 순수 정규식. **8종 + 체크섬 검증으로 확장**한다 ([ARCHITECTURE §5.1](ARCHITECTURE.md#51-엔티티-8종과-체크섬)). 추가: 외국인등록번호·여권·운전면허·사업자등록번호·법인등록번호. 기존 KR_RRN은 `\d{6}-[1-8]\d{6}` 형식 매칭뿐이라 체크섬이 없다 — 이 확장이 축 A의 실체다 |
| [src/dlp/vault_presidio.py](../../src/dlp/vault_presidio.py) | **ADAPT** | Presidio `Kr*Recognizer` 연동과 한국어 결함 패치(`supported_language` `"kr"`→`"ko"`, 한글 문맥 단어 추가)를 담고 있다. 새 구조에서 Presidio 경로의 기준점 |
| [eval/presidio_defects.py](../../eval/presidio_defects.py) + [results/presidio_defects.json](../../results/presidio_defects.json) | **KEEP** | **확인된 Presidio 한국어 결함 2건의 재현 코드와 증거.** 구 프로젝트에서 가장 확실한 원저 기여이며, 업스트림 PR 후보다 ([ROADMAP W6](ROADMAP.md#w6--버퍼--스트레치)) |
| `contains_raw()` (vault.py 내) | **KEEP** | 유출 검사. 다만 "볼트가 이미 매핑한 값만 잡는다"는 범위 한계가 코드에 정직하게 기록돼 있고, **그 기록을 반드시 유지한다** |

### 이식 시 반영할 알려진 한계

기존 코드가 스스로 기록한 두 가지를 새 설계에 반영한다.

- `restore()`가 **정확 매칭만** 복원한다 — 모델이 `<email_1>`이나 `< EMAIL_1 >`로 변형하면 복원 실패. 관용 매칭을 추가하되 오복원 위험 때문에 허용 범위를 테스트로 고정한다 ([ARCHITECTURE §5.2](ARCHITECTURE.md#52-가역-마스킹-볼트))
- `contains_raw()`의 범위 — 모델이 새로 생성했거나 탐지기가 놓친 PII는 잡지 못한다. "PII 유출 0%"는 *탐지·토큰화된 PII에 한정*한 주장이다

---

## 2. 정책·게이트 — 축 C의 개념 원본

| 경로 | 판정 | 새 위치 / 사유 |
|---|---|---|
| [src/agent/policy.py](../../src/agent/policy.py) `TierAGate` | **ADAPT** | 도구 스펙 → 인자 스키마 검증 → egress allowlist → taint → HITL 순의 결정 구조를 정책 테이블 엔진으로 이식. 판정이 `Block`/`None` 이진이던 것을 **4모드(allow/mask/warn/block)로 확장** ([ARCHITECTURE §7](ARCHITECTURE.md#7-정책-엔진)) |
| [src/agent/taint.py](../../src/agent/taint.py) | **ADAPT** | `Tainted(value, sources)` 래퍼와 재귀적 `sources_of()`. 더 중요한 것은 **value-provenance** 통찰 — 모델 라운드트립으로 래퍼가 세탁되므로(모델이 낸 args는 순수 문자열) 인자 값이 비신뢰 출력의 부분문자열인지를 별도 검사해야 실제로 작동한다. 이게 없으면 taint는 실 에피소드에서 발동하지 않는다 |
| [src/agent/hitl.py](../../src/agent/hitl.py) | **ADAPT** | 승인 큐 → `warn` 모드의 승인 대기 |
| [src/agent/policy.py](../../src/agent/policy.py) `DefenseHooks` | **REFERENCE** | 4-seam 조립 방식은 LiteLLM 훅으로 대체된다. 구조만 참고 |
| [config/policy.yaml](../../config/policy.yaml) | **REFERENCE** | 단일 진실원천 시도는 좋았으나 상당수 키가 실제로 읽히지 않았다(`agent.max_iterations`, `dlp:` 블록 전체, `eval:` 블록 전체 등). **선언과 실제 동작의 괴리**가 새 정책 스키마 설계 시 경계할 지점 |

### taint.py의 선행연구 표기 유지

기존 `taint.py`는 ARGUS(arXiv:2605.03378)가 taint provenance를 이미 발표했음을 명시하고 "신규성을 주장하지 않고 한국어·도구채널로 이식한다"고 적었다. **이 태도를 그대로 유지한다.** 새 문서에서도 CaMeL(arXiv:2503.18813)을 이론적 배경으로 인용하되 게이트웨이 레벨 경량 근사임을 밝힌다.

---

## 3. 평가 인프라 — 축 B의 엔진

| 경로 | 판정 | 새 위치 / 사유 |
|---|---|---|
| [eval/obfuscate.py](../../eval/obfuscate.py) | **KEEP** | 뮤테이터 6종(base64_wrap, homoglyph, jamo_split, emoji_smuggle, unicode_tag, zero_width). **KoTIP-v2의 난독화 엔진**. 여기에 초성체·로마자·한자혼용·조사파괴·존댓말 사회공학을 추가한다 |
| [eval/stats.py](../../eval/stats.py) | **KEEP** | Wilson 신뢰구간, McNemar exact, **cluster McNemar**. 평가 원칙 4조(n은 군집 단위로)를 지탱하는 코드 |
| [eval/check_contamination.py](../../eval/check_contamination.py) | **KEEP** | 도메인 중복 + n-gram Jaccard 오염 검사 |
| [eval/harness.py](../../eval/harness.py) | **ADAPT** | case → world → episode → 결정적 채점 구조. LLM judge를 쓰지 않는 점이 좋다. 게이트웨이 요청 단위로 재구성 |
| [eval/build_dataset.py](../../eval/build_dataset.py) | **ADAPT** | KoTIP 생성기. v2의 12+ 시나리오와 양성 코퍼스 생성으로 확장 |
| [data/kotip/v0.1/](../../data/kotip/v0.1/) attacks·utility | **REFERENCE** | KoTIP-v2의 **시드**. 그대로 쓰지 않는다 — 2 시나리오 × 5 스타일 = 10군집이고 utility가 2건뿐이라 [ROADMAP W4](ROADMAP.md#w4--kotip-v2와-양성-코퍼스)의 교정 대상이다 |
| [eval/run.py](../../eval/run.py), [eval/ablation.py](../../eval/ablation.py), [eval/expansion_v2.py](../../eval/expansion_v2.py) | **DROP** | 7-arm 애블레이션 내러티브 전용. 새 평가는 베이스라인 비교 구조 |
| [eval/detector_eval.py](../../eval/detector_eval.py) | **REFERENCE** | 난독화 붕괴 측정 방식은 참고. 단 한국어 negative set이 positive의 40자 접두사 중복제거본(positive 10 / negative 2)이라 F1·precision이 사실상 무의미했다 — **같은 실수를 반복하지 않기 위한 반면교사** |
| [eval/dlp_eval.py](../../eval/dlp_eval.py) | **ADAPT** | DLP 유출 측정. 단 n=8이었고 `model_view`가 모델 입력이 아니라 모델이 생성한 tool_args를 재는 지표였다는 점을 새 설계에서 명확히 분리 |
| [eval/data_spike.py](../../eval/data_spike.py) | **REFERENCE** | 7개 원격 데이터셋 로더 + DuckDB pushdown(4.27GB 풀 다운로드 회피). KDPII 로딩 시 같은 패턴 활용 가능 |
| [eval/kanana_compare.py](../../eval/kanana_compare.py) | **REFERENCE** | 채널갭 실측(user 발화 recall 1.0 vs chat template 0.0). Kanana를 어떤 배선으로 호출해야 작동하는지의 실증 |
| [eval/nemotron_projector.py](../../eval/nemotron_projector.py) | **DROP** | 영어 Nemotron 투영. 새 방향에서 쓰지 않음 |
| [eval/progress.py](../../eval/progress.py) | **REFERENCE** | 진행 표시 유틸 |

---

## 4. 서비스·런타임

| 경로 | 판정 | 새 위치 / 사유 |
|---|---|---|
| [src/agent/server.py](../../src/agent/server.py) | **ADAPT** | principal 기반 authz, X-API-Key 레지스트리, idempotency store, 감사 마스킹, 프로덕션 부팅 fail-closed. **관리·감사 API 사이드카**로 이식 ([ARCHITECTURE §2](ARCHITECTURE.md#2-인터셉션-지점-결정)) |
| [src/agent/authz.py](../../src/agent/authz.py) | **ADAPT** | `Principal`, 프로덕션에서 데모 키 거부, principal↔customer 바인딩(confused deputy 차단) |
| [src/agent/metrics.py](../../src/agent/metrics.py) | **KEEP** | 카디널리티 가드가 있는 in-process 메트릭, **PII-free 스냅샷**. 볼트가 엔티티 타입만 카운트하고 원본 값은 기록하지 않는 패턴이 여기 연결돼 있다 |
| [src/agent/log.py](../../src/agent/log.py) | **ADAPT** | 구조화 JSON 로그(PII-free) → 감사 로그 스키마 |
| [src/agent/trace.py](../../src/agent/trace.py) | **REFERENCE** | `HopTrace`/`EpisodeTrace`. 게이트웨이는 홉 구조가 다르므로 개념만 |
| [src/agent/loop.py](../../src/agent/loop.py) | **DROP** | 자체 ReAct 루프. LiteLLM이 대체한다 |
| [src/agent/tools.py](../../src/agent/tools.py), [toolworld.py](../../src/agent/toolworld.py), [backend.py](../../src/agent/backend.py) | **DROP** | mock 도구 세계. 게이트웨이는 도구를 실행하지 않는다 |
| [src/agent/spotlight.py](../../src/agent/spotlight.py) | **REFERENCE** | Spotlighting 프롬프트 재작성. 구현이 부분적이었다(문자열만 래핑해서 `list[dict]`를 반환하는 도구에는 적용 안 됨). 개념은 유효하므로 필요 시 재구현 |
| [src/agent/cli.py](../../src/agent/cli.py), [demo_runtime.py](../../src/agent/demo_runtime.py), [workflow.py](../../src/agent/workflow.py) | **DROP** | 구 데모 경로 |
| [src/agent/hooks.py](../../src/agent/hooks.py) | **DROP** | 4-seam 훅 → LiteLLM 훅 |
| [app/ui.py](../../app/ui.py), [app/trace_view.py](../../app/trace_view.py) | **DROP** | 구 데모 UI. `trace_view.py`의 출력 대상(`report/`)은 이미 삭제됐다 |

---

## 5. 프로바이더

| 경로 | 판정 | 사유 |
|---|---|---|
| [src/providers/gemini.py](../../src/providers/gemini.py) | **REFERENCE** | google-genai 클라이언트 설정, 4개 HarmCategory 명시 지정, 모든 functionCall part 순회, `thought_signature` 보존. **Gemini 업스트림 구성 시 유용한 실무 지식** |
| [src/providers/cassette.py](../../src/providers/cassette.py), [fake.py](../../src/providers/fake.py) | **ADAPT** | sha256 키 record/replay와 손저작 카세트 거부. **키 없는 재현 가능 데모**에 유용하다 |
| [src/providers/base.py](../../src/providers/base.py), [openai_provider.py](../../src/providers/openai_provider.py), [_resilience.py](../../src/providers/_resilience.py), [recording.py](../../src/providers/recording.py) | **DROP** | LiteLLM이 프로바이더 추상화를 대체한다 |

---

## 6. 가드 레일

| 경로 | 판정 | 사유 |
|---|---|---|
| [src/rails/kanana.py](../../src/rails/kanana.py) | **ADAPT** | kanana-safeguard-prompt-2.1b 통합 코드. 3개 taxonomy 단일 토큰(SAFE/A1/A2) 채점 방식 그대로 쓴다. **L1-INJ 레일의 출발점** |
| [src/rails/base.py](../../src/rails/base.py) | **ADAPT** | `RailScorer` 프로토콜 + `HeuristicRail` 정규식 폴백. GPU 부재 시 폴백 경로 |
| [src/rails/ko_agentguard.py](../../src/rails/ko_agentguard.py) | **REFERENCE** | TF-IDF + LR. 영어 학습 결과가 한국어에서 F1 0.0이었고 base64에서도 0.0으로 붕괴했다. **왜 자체 학습 분류기를 강제 경계로 쓰지 않는지의 증거** |
| [src/rails/shieldgemma.py](../../src/rails/shieldgemma.py) | **DROP** | 영어 중심 + 게이팅 모델. 새 스택에서 기각 ([MARKET_RESEARCH §6.2](MARKET_RESEARCH.md#62-검토-후-기각)) |
| [src/rails/e5_lr.py](../../src/rails/e5_lr.py) | **DROP** | 기본 `head_path`가 존재하지 않는 파일을 가리켜 실제로 실행되지 않는다(가중치는 `models/gpu_trained/`에 있음). EN F1 0.954 / KO 0.0 결과만 REFERENCE로 인용 |

---

## 7. 학습 코드와 모델

| 경로 | 판정 | 사유 |
|---|---|---|
| [eval/train_pii_ner.py](../../eval/train_pii_ner.py) | **REFERENCE** | klue/roberta-small 토큰 분류 학습. **결과(span F1 0.9845)는 row-level random split의 누수 가능 상한**이므로 헤드라인으로 쓸 수 없다. 재학습 가치는 [ROADMAP Q7](ROADMAP.md#결정-대기-open-questions)에서 판단 |
| [eval/train_harm.py](../../eval/train_harm.py) | **DROP** | K-MHaS 다중 라벨 유해성. Kanana-8b(F1 0.946)가 대체한다 |
| [eval/train_rail.py](../../eval/train_rail.py) | **REFERENCE** | 난독화 붕괴 측정 루틴은 참고. 길이 shortcut 경고(길이만 쓴 LR이 F1 0.957)를 남긴 것이 좋은 자기점검이었다 |
| `models/gpu_trained/` (gitignored, 682MB) | **REFERENCE** | RunPod 학습 산출물. 새 방향에서 로드하지 않는다 |
| [scripts/run_gpu.sh](../../scripts/run_gpu.sh) | **DROP** | RunPod 런북 |
| [scripts/fetch_rail.py](../../scripts/fetch_rail.py), [push_hf.py](../../scripts/push_hf.py) | **ADAPT** | SHA256 검증 다운로드 / 라이선스 allowlist 강제 업로드. 벤치마크 공개 시 재사용 ([ROADMAP Q3](ROADMAP.md#결정-대기-open-questions)) |
| [scripts/check_env.py](../../scripts/check_env.py) | **ADAPT** | 환경 프리플라이트. Gemini 함수 호출 스모크 |
| [scripts/monitor.py](../../scripts/monitor.py) | **REFERENCE** | stdlib만 쓰는 터미널 모니터 |
| [scripts/backfill_run_meta.py](../../scripts/backfill_run_meta.py) | **DROP** | 구 결과에 provenance를 소급 부착하던 일회성 스크립트. **provenance 메커니즘이 그것이 보호해야 할 수치보다 나중에 만들어졌다는 사실 자체가 교훈** |
| [scripts/run_demo.py](../../scripts/run_demo.py), [smoke_live.py](../../scripts/smoke_live.py) | **DROP** | 구 데모 경로 |

---

## 8. 테스트

| 경로 | 판정 | 사유 |
|---|---|---|
| [tests/test_vault_scope.py](../../tests/test_vault_scope.py) | **KEEP** | 요청 스코프 coreference, 요청 간 유출 없음. 볼트 이식 후에도 그대로 유효 |
| [tests/test_dlp_defenses.py](../../tests/test_dlp_defenses.py) | **ADAPT** | Presidio 결함 + 패치 검증. 새 recognizer 스위트 테스트의 출발점 |
| [tests/test_gate.py](../../tests/test_gate.py) | **ADAPT** | allowlist·서브어드레싱·스키마·taint 케이스. 4모드 정책 테스트로 확장 |
| [tests/test_metrics.py](../../tests/test_metrics.py) | **KEEP** | 카디널리티 가드, **PII-free 스냅샷** 검증 |
| [tests/test_service_integration.py](../../tests/test_service_integration.py), [test_production_hardening.py](../../tests/test_production_hardening.py), [test_multitenant_hardening.py](../../tests/test_multitenant_hardening.py) | **ADAPT** | HITL 승인·자기승인 거부·idempotency·테넌트 격리. 관리 API 사이드카로 이식 |
| [tests/test_eval_metrics.py](../../tests/test_eval_metrics.py) | **ADAPT** | cluster McNemar, **fail-loud 레일(조용한 폴백 금지)** 검증. 후자는 새 설계의 `fallback_used` 로그와 직결 |
| [tests/test_rail_cannot_override_gate.py](../../tests/test_rail_cannot_override_gate.py) | **REFERENCE** | 분류기가 결정적 게이트를 덮어쓸 수 없다는 스키마 봉인. 새 구조에서는 캐스케이드 순서로 표현된다 |
| [tests/test_loop_d1.py](../../tests/test_loop_d1.py), [test_workflow_ui.py](../../tests/test_workflow_ui.py), [test_demo.py](../../tests/test_demo.py), [test_server.py](../../tests/test_server.py), [test_runtime_hardening.py](../../tests/test_runtime_hardening.py) | **DROP** | 구 ReAct 루프·데모 UI 전용 |

---

## 9. 결과 자산 (인용용)

새 문서에서 사실로 인용할 수 있는 구 프로젝트의 실측이다. 전부 **REFERENCE**이며, 인용 시 원 조건(n, split 방식, 하드웨어)을 함께 적는다.

| 결과 | 수치 | 인용 맥락 | 주의 |
|---|---|---|---|
| [results/presidio_defects.json](../../results/presidio_defects.json) | 결함 2건 확인 | Presidio 한국어 미비의 직접 증거 | 가장 신뢰할 만한 원저 기여 |
| [results/channel_gap.json](../../results/channel_gap.json) | Kanana recall: user 발화 1.00 / chat template 0.00 / raw tool 문자열 1.00 | Kanana를 **어떤 배선으로 호출해야 하는가**의 실증 | n=10 |
| [results/detector_multi.json](../../results/detector_multi.json) | 영어 학습 TF-IDF+LR: 영어 in-domain F1 0.9936 → base64 0.000, 한국어 0.000 | 자체 학습 분류기를 강제 경계로 쓰지 않는 근거 | 한국어 negative set이 degenerate(positive 10 / negative 2, 접두사 중복)라 **F1·precision은 무의미**. recall=0 신호만 유효 |
| [results/e5_crosslingual.json](../../results/e5_crosslingual.json) | multilingual-e5-small + LR: EN F1 0.954 → KO 0.0 | 다국어 인코더도 한국어 도메인 이전이 안 된다 | 한국어 negative set 근사 |
| [results/r6_pii_ner.json](../../results/r6_pii_ner.json) | klue/roberta-small span F1 0.9845 | 한국어 PII NER의 가능성 | **row-level random split — 누수 가능 상한.** 헤드라인 불가 |
| [results/contamination.json](../../results/contamination.json) | max 5-gram Jaccard 0.419 < 0.60, GREEN | 오염 검사 절차의 선례 | |
| [results/RESULTS.md](../../results/RESULTS.md) | 전체 서술 | 무엇이 왜 실패했는지의 1차 기록 | 헤드라인 t-ASR 서사는 인용하지 않는다 |

**인용하지 않을 것**: t-ASR 0.167 → 0.000 헤드라인(동어반복 구조), 군집 p=0.125가 n.s.임에도 유지된 theater 주장, spotlight 0.25(부분 구현 상태의 수치).

---

## 10. 저장소 잔재

| 항목 | 조치 |
|---|---|
| [results/runs.jsonl.bak](../../results/runs.jsonl.bak) (188KB) | 구현 착수 시 제거 |
| [data/cassettes/cassettes.json](../../data/cassettes/cassettes.json) | 구 데모 카세트. 새 데모 카세트로 교체 |
| `data/kotip/v0.1/*.smoke.jsonl` | 생성 코드가 없는 수작업 부분집합(`eval/build_dataset.py`는 dev/test만 생성). v2에서는 생성 코드 없는 데이터를 만들지 않는다 |
| [results/e5_crosslingual.json](../../results/e5_crosslingual.json), [shieldgemma_enko.json](../../results/shieldgemma_enko.json) | 저장소에 생성 스크립트가 없다(RunPod 출력 수작업 복사). 인용 시 이 사실을 밝힌다 |
| `results/traces.jsonl` | [eval/run.py](../../eval/run.py)가 기록한다고 선언했으나 파일이 없다. 헤드라인 실행의 홉 단위 트레이스가 소실됐다 |
| [requirements.txt](../../requirements.txt) / [requirements.core.txt](../../requirements.core.txt) | **ADAPT** — core/full 이원화 패턴은 유지. 내용은 새 스택(LiteLLM·Presidio·transformers)으로 교체 |
| [.github/workflows/ci.yml](../../.github/workflows/ci.yml) | **ADAPT** — core lock 설치 → compileall → 정책 스키마 불변식 → pytest 구조 유지. 불변식 내용은 교체 |

---

## 요약

| 판정 | 개수 | 핵심 |
|---|---|---|
| KEEP | 8 | 볼트 코어, Presidio 결함 증거, obfuscate, stats, contamination, metrics, vault 테스트 2종 |
| ADAPT | 20 | recognizers(5→8+체크섬), TierAGate(2모드→4모드), taint, hitl, server/authz, kanana rail, harness, build_dataset 등 |
| REFERENCE | 15 | 실측 결과, gemini 클라이언트 실무 지식, 반면교사 사례 |
| DROP | 20 | ReAct 루프, mock 도구 세계, 프로바이더 추상화, shieldgemma/e5, 구 데모·애블레이션 경로 |

**축별 재사용 밀도**

- **축 A (한국형 PII + 볼트)** — 가장 높다. 볼트가 KEEP이고 recognizer는 확장만 하면 된다. Presidio 결함 2건은 그대로 기여 자산이다.
- **축 C (tool-call DLP)** — 개념 이식이 핵심이다. `TierAGate`의 결정 순서와 `taint.py`의 value-provenance 통찰이 새 층으로 그대로 옮겨간다. **구 프로젝트의 피벗이 폐기가 아니라 재배치가 되는 지점.**
- **축 B (벤치마크)** — `obfuscate.py`와 `stats.py`가 엔진이고, 데이터는 시드로만 쓴다(구조적 결함 때문에 그대로 못 쓴다).
