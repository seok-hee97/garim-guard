# 가림(Garim) Gateway

> **한국어 특화 오픈소스 GenAI DLP 게이트웨이** — 프롬프트와 tool-call 양쪽에서 한국어 개인정보를 체크섬 검증으로 탐지·가역 마스킹하고, **오탐률(FPR)까지 공개 벤치마크로 증명한다.**
>
> **상태: 기획 단계 (구현 전)** · Python · 솔로 · 2026-07 재기획

이 저장소는 현재 **기획 문서**만 담고 있다. `src/`·`eval/`·`data/`에 있는 코드는 이전 방향(SafeAgent-KR)의 산출물이며, [자산 처분 맵](docs/plan/ASSET_REUSE.md)에 따라 선별적으로 이식될 예정이다.

---

## 왜 방향을 바꿨는가

원 과제 브리프([CLAUDE.md](CLAUDE.md))는 **안랩클라우드메이트 SecureBridge와 같은 솔루션**을 참고해 Safety AI / Guardrail / DLP 주제를 기획하라고 했고, Google 모델과 온디바이스 모델을 우선하라고 했다. 이전 프로젝트(SafeAgent-KR)는 그 요구에서 세 가지가 어긋났다.

1. **참고 솔루션을 한 번도 분석하지 않았다.** 전 문서를 통틀어 SecureBridge 언급이 0회였고, 대신 연구용 에이전트 안전 런타임으로 방향이 옮겨갔다.
2. **헤드라인 지표가 구성상 참이었다.** allowlist 밖 주소로의 발송을 공격 성공으로 정의한 뒤 allowlist를 켜서 0%를 얻는 구조였다. 평가 규모도 공격 60건이 실질 10군집, 정상 케이스는 2건이었다.
3. **Google·온디바이스 요건을 논지로 반박했다.** 헤드라인 실측은 전부 gpt-4o-mini로 냈고, "온디바이스 분류기를 강제 경계로 쓰면 안 된다"가 프로젝트의 핵심 주장이 됐다.

새 방향은 세 가지를 정면으로 교정한다. SecureBridge 기능 분해를 첫 문서에 두고, 헤드라인 지표를 **재현율 + FPR을 같은 표에** 싣는 것으로 바꾸며, 탐지 모델을 전부 로컬에서 돌리고 생성만 Gemini로 보낸다.

---

## 무엇을 만드는가

**사용자의 PC에서 도는 단일 프로세스**이고, 그 안에 심(seam)이 둘 있다. LLM 프록시만 두면 도구를 실행하지 않으므로 차단이 권고에 그치기 때문입니다.

```
┌──────────── 클라이언트 PC ────────────┐
│  앱 · 에이전트 · IDE                    │
│      │ localhost:8080 (OpenAI 호환)    │
│      ▼                                │
│  가림 로컬 게이트웨이                     │
│   ├ L0 정규식+체크섬      CPU  <10ms    │
│   ├ L1 PII NER          CPU  ~40ms    │
│   ├ L1 인젝션 분류기      CPU  ~15ms    │
│   ├ 볼트 · taint · trifecta            │
│   └ MCP 게이트웨이 ──► 도구 (실제 차단 지점) │
│      │                                │
│      │ ★ 이 경계를 넘는 건 마스킹된 것뿐 ★  │
└──────┼───────────────────────────────┘
       ▼
  Gemini API · OpenAI API · 로컬 Gemma 4
```

**탐지 모델은 전부 사용자 PC에서 돕니다.** 그래서 원문 PII를 보는 주체가 사용자 본인뿐이고, 패킷 캡처로 증명할 수 있습니다. 대가는 조직 차원의 강제가 약해지는 것 — 사용자가 끌 수 있습니다. IT가 전사에 강제하는 통제가 아니라 개발자가 자기 PC에서 켜는 안전장치입니다.

| 축 | 내용 |
|---|---|
| **A. 한국형 PII 스위트 + 가역 볼트** | 주민등록번호·외국인등록번호·여권·운전면허·사업자등록번호·법인등록번호·계좌·카드 8종을 **체크섬 검증**과 함께 탐지. 조사 결합을 처리하는 가역 마스킹. 체크섬은 형식만 맞는 무작위 숫자열의 약 90%를 걸러낸다(오탐 약 1/10) |
| **C. tool-call / MCP DLP** | 도구 신뢰 등급에 따른 조건부 PII 복원, description 핀닝(rug pull 방어), **Lethal Trifecta 흐름 판정**으로 다단계 유출 차단 |
| **B. 한국어 인젝션 벤치마크** (평가 인프라) | 자모분해·초성체·로마자·한자혼용·존댓말 사회공학 등 한국어 고유 우회 벡터. **공개 코퍼스가 존재하지 않는 영역** |

**시장에서의 좌표**: 국내에 같은 범주의 상용 제품이 최소 8종 있지만 **오탐률을 공개한 곳이 하나도 없다.** 한국 PII 탐지기가 아예 없는 것은 아니다 — Google Cloud SDP에 한국 infoType 6종이 있고 Model Armor는 한국어를 공식 지원한다. **다만 둘 다 프롬프트를 클라우드로 보낸다.** 그래서 갭은 "탐지기가 없다"가 아니라 **"데이터를 내보내지 않고 쓸 수 있는 탐지기가 없다"** 이다. 근거는 [시장 조사 편람](docs/plan/MARKET_RESEARCH.md)에 있다.

**범위 밖**: Shadow AI 앱 카탈로그, 브라우저 확장, 네트워크 인라인 SSL 인터셉션, OCR, 웹 관리 콘솔, OAuth 2.1 Resource Server 전체 구현. 그리고 **MCP를 통과하지 않는 도구는 보지 못한다.** 못 하는 것을 아는 것도 설계의 일부이므로 [기획서 §1.4](docs/plan/기획서.md#14-범위-in--out)에 표로 명시했다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [기획서](docs/plan/기획서.md) | 배경·목표·범위·성공 지표·평가 계획·리스크. [TEMP.md](TEMP.md)의 7단 양식 |
| [시장 조사 편람](docs/plan/MARKET_RESEARCH.md) | SecureBridge 기능 분해, 국내 8개 제품 비교, 글로벌 동향, 시장 갭 5개, 규제 매핑, 컴포넌트·라이선스 |
| [모델·학습 계획](docs/plan/MODEL_AND_TRAINING.md) | **로컬 우선 배포, 모델별 메모리·지연 예산, 학습 데이터 라이선스 심사, 파인튜닝 2건, 누수 방지 평가** |
| [에이전트 시스템 설계](docs/plan/AGENT_SYSTEM.md) | **두 심 구조, 세션 상태, 도구 신뢰 등급, Lethal Trifecta, MCP 게이트웨이 상세, 데모 시나리오.** 재검토에서 찾은 설계 결함 4건의 교정을 담는다 |
| [아키텍처](docs/plan/ARCHITECTURE.md) | 가드 캐스케이드와 레이턴시 예산, 정책 엔진, 감사 스키마, 온디바이스 분리 |
| [로드맵](docs/plan/ROADMAP.md) | 5주 + 버퍼 주차별 계획과 검증 기준, 평가 원칙 5조, 결정 대기 8건 |
| [자산 처분 맵](docs/plan/ASSET_REUSE.md) | 구 프로젝트 코드 63개 항목의 KEEP / ADAPT / REFERENCE / DROP 판정 |

---

## 기술 스택 (계획)

탐지는 전부 로컬에서, 생성만 외부로. PII를 탐지하려고 PII를 외부 API로 보내는 것은 자기모순이기 때문이다.

| 역할 | 컴포넌트 | 라이선스 |
|---|---|---|
| 게이트웨이 본체 | LiteLLM proxy | MIT |
| PII 프레임워크 | Presidio (+ 직접 작성한 한국어 recognizer) | MIT |
| PII span 추출 | `FrameByFrame/privacy-filter-korean` (active 50M, CPU) | Apache 2.0 |
| 인젝션 레일 | `kakaocorp/kanana-safeguard-prompt-2.1b` | Apache 2.0 |
| 비동기 감사 라벨링 | `kanana-safeguard-8b` / `-siren-8b` | Apache 2.0 |
| MCP 게이트웨이 | FastMCP v3 Proxy + Middleware | Apache 2.0 |
| 데모 에이전트 | Google ADK (`McpToolset`) | Apache 2.0 |
| 업스트림 생성 | **Gemini API** (`gemini-3.6-flash`) + **Gemma 4 E4B 로컬** | Apache 2.0 (Gemma 4) |

채택 스택 전체가 MIT 또는 Apache 2.0이라 상용 배포까지 제약이 없다. **Gemma 4는 2026-03 출시로 Gemmaverse 최초 Apache 2.0** — Gemma 3까지의 ToU 전파 의무·해지 조항이 사라졌다. 검토 후 기각한 후보와 사유는 [MARKET_RESEARCH §6.2](docs/plan/MARKET_RESEARCH.md#62-검토-후-기각)에 있다.

---

## 이전 프로젝트의 자산

구 SafeAgent-KR의 마지막 완전 상태는 커밋 `74e1ae7`에 봉인돼 있다. 삭제된 기획·발표 문서는 git 이력에서 볼 수 있다.

새 방향으로 이어지는 것 중 값이 있는 것:

- **Presidio 한국어 결함 2건** — `KrPassportRecognizer.supported_language`가 ISO-3166 국가코드 `"kr"`로 잘못 설정돼 `language="ko"` 파이프라인에서 로드되지 않는 문제, `KrRrnRecognizer.context`에 한글 문맥 단어가 없는 문제. 재현 코드와 증거가 남아 있고 업스트림 PR 후보다
- **가역 볼트** ([src/dlp/vault.py](src/dlp/vault.py)) — 요청 스코프 토큰화와 값 단위 coreference
- **value-provenance 통찰** ([src/agent/taint.py](src/agent/taint.py)) — 모델 라운드트립으로 taint 래퍼가 세탁되므로, 인자 값이 비신뢰 출력의 부분문자열인지를 별도로 검사해야 실제로 작동한다
- **난독화 뮤테이터 6종** ([eval/obfuscate.py](eval/obfuscate.py))와 **군집 인지 통계** ([eval/stats.py](eval/stats.py))
- **Kanana 채널갭 실측** — 같은 인젝션이 사용자 발화로는 recall 1.0인데 chat template을 거치면 0.0이 된다(n=10). 모델을 어떤 배선으로 호출해야 하는지의 실증

인용 시 원 조건(n, split 방식, 하드웨어)을 함께 적는 규칙과, 인용하지 않을 결과 목록은 [ASSET_REUSE §9](docs/plan/ASSET_REUSE.md#9-결과-자산-인용용)에 있다.

---

## 라이선스

미정. 구현 착수 시 결정한다.
