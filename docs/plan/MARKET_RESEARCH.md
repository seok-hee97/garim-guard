# 시장·규제·컴포넌트 조사 편람

> 조사일 2026-07-31 · 공개 웹 자료(벤더 페이지·보도자료·언론·논문·모델 카드)만 사용
> 벤더가 자체 발표한 수치는 **[벤더 주장]** 으로 표기했다. 직접 벤치마크하지 않았다.
>
> 이 문서는 [기획서](기획서.md) · [ARCHITECTURE](ARCHITECTURE.md) · [ROADMAP](ROADMAP.md)이 참조하는 근거 문서다.

---

## §1. 참고 솔루션 — SecureBridge (안랩클라우드메이트)

원 과제 브리프([CLAUDE.md](../../CLAUDE.md))가 지목한 참고 솔루션이다. 구 프로젝트는 이 제품을 한 번도 분석하지 않은 채 진행됐고, 그것이 재기획의 직접적인 계기다. 따라서 이 절을 문서 첫 장에 둔다.

### 1.1 기본 정보

| 항목 | 내용 |
|---|---|
| 벤더 | 안랩클라우드메이트 (안랩의 AX·MSP 자회사) |
| 제품명 | 시큐어브리지 / SecureBridge |
| 카테고리 | 생성형 AI 데이터 보안 — **에이전트리스 AI 게이트웨이형 프롬프트 DLP + 거버넌스** |
| 출시 | 2025-06-16 |
| 제품 페이지 | https://ahnlabcloudmate.com/ai-and-data/securebridge |

### 1.2 기능 분해

공개 자료에서 확인되는 기능을 네 덩어리로 나눈다. 오른쪽 열은 본 프로젝트가 같은 기능을 다루는지에 대한 판정이며, 근거는 [기획서 §1 범위표](기획서.md#14-범위-in--out)에 있다.

| 기능군 | SecureBridge가 하는 일 | 본 프로젝트 |
|---|---|---|
| **데이터 통제** | 프롬프트 입력값에서 CI(기업기밀)·PII(개인정보)·SI(민감정보) 탐지·차단. LLM 기반 탐지 엔진 | ✅ 다룸 (더 좁고 더 깊게 — 한국 고유 식별자 8종 + 체크섬 + 가역 마스킹) |
| **공격 방어** | 프롬프트 인젝션 차단, 입력·응답 양방향 통제 | ✅ 다룸 (입력 레일 + tool-call 층 확장) |
| **가시성·거버넌스** | 프롬프트 이력 모니터링, 정책 중앙 관리, 신규 사용자·단말 자동 정책 적용, 감사 추적. 어드민 콘솔 로그: 사용자 ID·질의 시각·원본 프롬프트·탐지 결과·상세 내역 | ⚠️ 부분 (감사 로그 스키마 + CLI. 웹 콘솔은 범위 외) |
| **Shadow AI** | 알려진 AI 애플리케이션 1,200종 이상 식별 [벤더 주장] | ❌ 범위 외 (앱 카탈로그는 조직 규모의 문제 — [기획서 §1.4](기획서.md#14-범위-in--out) 참조) |

### 1.3 아키텍처

공개 자료를 종합하면 **엔드포인트 에이전트가 없는 게이트웨이(중계) 방식**이다.

- 제품 페이지의 데이터 흐름: `사용자 → AI Gateway(사용자 식별·접근 통제·트래픽 분석) → SecureBridge 탐지 엔진 → LLM 서비스 → 응답 재검사 → 사용자`
- 발표 자료의 "Guardian Architecture": 입력부터 응답까지를 하나의 프로세스로 보고 제로트러스트 원칙에 따라 **PEP(정책 시행 지점)과 PDP(정책 결정 지점)를 분리** 배치
- CTO 인터뷰 요지: 에이전트는 프로그램 제어를 위해 운영체제에 관여하려고 설치하는데 생성형 AI는 외부 서버와의 통신이 주된 구조이므로 에이전트리스가 맞다는 논리
- 네트워크 계층에서 동작하며 오픈 API로 LLM 및 기업 IAM/DLP와 연동
- 공공 조달 등록 기사: "별도의 서버 구축이나 클라이언트 설치 없이 브라우저에서 바로 사용"

> ⚠️ **확정 불가 구간**: 위의 "브라우저에서 바로 사용"이 (a) 자체 제공 웹 채팅 포털인지, (b) 포워드 프록시/SWG 연동으로 기존 chatgpt.com 트래픽을 가로채는지, (c) 브라우저 확장이 있는지는 공개 자료로 판단할 수 없다. 브라우저 확장 배포 근거는 찾지 못했다. **본 프로젝트는 이 모호함을 피해 API 레이어(OpenAI 호환 프록시)를 인터셉션 지점으로 명시 선택한다** — [ARCHITECTURE §2](ARCHITECTURE.md#2-인터셉션-지점-결정) 참조.

### 1.4 지원 LLM·배포·시장 지위

- **지원 LLM** (제품 페이지 명시): ChatGPT, Microsoft Copilot, Google Gemini, 네이버 HyperCLOVA X, Anthropic Claude, Meta Llama — 특정 모델에 종속되지 않는 멀티모델 연동
- **배포**: On-Premise / SaaS / Hybrid 3종
- **조달 채널**: 디지털서비스 이용지원시스템 등록(2026-02) → 공공기관 수의계약 가능 · 2026년 AI 바우처 공급 솔루션
- **2026-07-01 과기정통부·KISA 「2026년 우수 정보보호기술」 지정.** 지정 사유로 명시된 2대 핵심 기술:
  1. **AI 기반 문맥 내 개인정보 판단 기술** — 형식·키워드 매칭이 아니라 문장 전체 맥락과 정보 간 연관성을 보고 위험 수준 평가
  2. **다중 필터링 기반 데이터 보호 기술** — 복수의 탐지기·필터를 조합해 오탐 최소화
- **가격**: 공개된 정보 없음

> 위 2대 핵심 기술은 본 프로젝트의 설계 방향과 정확히 겹친다. ①은 정규식만으로 부족하다는 뜻이고(→ [ARCHITECTURE §4](ARCHITECTURE.md#4-가드-캐스케이드와-레이턴시-예산)의 정규식+체크섬+NER 캐스케이드), ②는 오탐이 이 시장의 핵심 난제라는 뜻이다(→ [기획서 §2](기획서.md#2-목표-및-성공-지표)에서 FPR을 헤드라인 지표로 승격한 근거). 다만 SecureBridge는 오탐률 수치를 공개하지 않는다 — 이것이 §4의 갭 ⑤다.

---

## §2. 국내 경쟁 지형

### 2.1 제품별 요약

| 벤더 / 제품 | 카테고리 | 인터셉션 지점 | 눈여겨볼 점 |
|---|---|---|---|
| **안랩클라우드메이트 SecureBridge** | AI 게이트웨이 | 네트워크/게이트웨이 | 에이전트리스, Shadow AI 1,200종+, 공공 조달 3종 세트 |
| **파수 AI-R DLP / AI-R Privacy / AI-R DHC** | 프록시 DLP | 외부 AI로 나가는 웹 트래픽 | 패턴매칭 + 트랜스포머 NLP 병용. **AI-R DHC는 AI 생성 결과물(출력)을 권한 기준으로 통제** — 입력만 보는 경쟁사와 다른 유일한 축 |
| **지란지교소프트 OfficeKeeper EP / AX Works** | 엔드포인트 DLP + LLM 프록시 게이트웨이 | PC 엔드포인트 + 메일 | **허용 / 차단 / 비식별화(마스킹) 3모드** — 차단 일변도가 아님. AI + 이메일 채널 통합 감사. 폐쇄망 중소기업 타깃 |
| **소만사 WebKeeper** | SWG 내장 AI DLP | 네트워크 프록시 | 2023년부터 챗GPT 통제 — 국내 최장 레퍼런스. 프롬프트·응답 대화를 메신저 형태 로그로 저장 |
| **컴트루테크놀로지 Sphinx AI** | 프록시 + API 이중 구조 | 웹 프록시 / API | **OCR로 이미지 내용 분석**(스크린샷 우회 대응), GitHub Copilot·Cursor 등 개발자 툴 커버, 가명처리 모드 |
| **윈스 SNIPER AIVAX** | 네트워크 어플라이언스 | 네트워크 + **MCP** | 프롬프트·**MCP**·네트워크 3계층. 국내에서 MCP 보안을 명시하는 거의 유일한 제품. N2SF 정합으로 공공 정조준 |
| **이로운앤컴퍼니 SAIFE X** | 거버넌스 + 중계 플랫폼 | 플랫폼 중계 | **TAPD — 유해성과 탈옥 가능성을 분리 평가**. 한국어 환경 97%+ 정확도 [벤더 주장]. 국내 유일에 가까운 한국어 특화 인젝션·탈옥 탐지 |
| **세이퍼존 AI-DLP v10** | 엔드포인트 통합 DLP | PC (Win/mac/Linux) | 국내 최초 3-OS 엔드포인트 동시 지원 |

플랫폼 내장형(별도 판매 아님): 삼성SDS FabriX/Brity Copilot(가드레일 + AI 레드팀, 기술블로그에 자체 가드레일 SGuard 공개), LG CNS DAP GenAI(사전 등록 보안 필터로 주민번호·전화번호 비식별화).
서비스형: SK쉴더스 LLM 취약점 진단 가이드(14개 취약점 3단계 위험도), S2W AI 서비스 진단, 에임인텔리전스(2026-04 시리즈A 100억, 레드팀 + 가드레일).

### 2.2 기능 매트릭스

◎ 강점 명시 / ○ 지원 확인 / △ 부분·불명확 / ✕ 해당 없음

| 제품 | 마스킹 | 감사 | 인젝션 방어 | Shadow AI | MCP·에이전트 | 온프렘 | 오탐률 공개 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| SecureBridge | △ | ○ | ○ | ◎ 1,200종+ | ✕ | ○ | ✕ |
| 파수 AI-R | ○ | ○ | △ | △ | ✕ | ○ | ✕ |
| 지란지교 OfficeKeeper | ◎ 3모드 | ◎ AI+메일 | △ | ○ | ✕ | ◎ 폐쇄망 | ✕ |
| 소만사 WebKeeper | △ | ◎ 대화로그 | △ | ○ | ✕ | ○ | ✕ |
| 컴트루 Sphinx AI | ◎ 가명처리 | ○ | ○ | △ | ✕ | ○ | ✕ |
| 윈스 SNIPER AIVAX | △ | ○ | ○ | △ | ◎ | ◎ N2SF | ✕ |
| 이로운앤컴퍼니 SAIFE X | ○ | ○ | ◎ 한국어 특화 | ◎ | ✕ | △ | ✕ |
| 세이퍼존 AI-DLP | △ | ○ | ✕ | △ | ○ | ○ | ✕ |

**오탐률 열이 전부 ✕라는 사실이 이 표에서 가장 중요하다.** 국내 어느 벤더도 FPR을 수치로 공개하지 않으며, 공개 비교 벤치마크도 없다. 그런데 현장에서는 과도한 DLP 정책이 정상 업무 오탐을 만들어 경보 피로를 부르고 결국 "DLP는 업무 효율 저해 장치"라는 인식으로 이어진다는 지적이 나온다. 갭 ⑤가 여기서 나온다.

---

## §3. 글로벌 동향 (2025–2026)

국내 시장이 어디로 향하는지의 선행 지표로만 참조한다. 본 프로젝트가 이들과 경쟁하는 것은 아니다.

### 3.1 카테고리 재편 — 독립 스타트업 시장의 종료

| 시점 | 인수자 | 피인수 | 규모 |
|---|---|---|---|
| 2024-10 | Cisco | Robust Intelligence | 비공개 → Cisco AI Defense |
| 2025-07 | Palo Alto | Protect AI | 비공개 → Prisma AIRS 근간 |
| 2025-09 | SentinelOne | Prompt Security | 발표 $250M |
| 2025-09 | Cato Networks | Aim Security | 보도 ~$350M |
| 2025-09 | F5 | CalypsoAI | $180M |
| 2025 Q4 | Check Point | Lakera | 보도 ~$300M |
| 2025-11 | Zscaler | SPLX | 비공개 |
| **2026-05** | **Palo Alto** | **Portkey** | 비공개 → **AI Gateway가 Prisma AIRS의 중추로 편입** |

독립 잔존: WitnessAI(2026-01 시리즈B $58M, 삼성벤처투자 참여), Harmonic Security, HiddenLayer, Nightfall, Arthur(Engine을 오픈소스화).

> **Portkey 인수의 함의**: LLM-ops 인프라(AI 게이트웨이)와 보안 제품의 경계가 사라졌다. 게이트웨이가 곧 보안 통제점이라는 시장 합의다. 본 프로젝트가 게이트웨이 형태를 택하는 근거이기도 하다.

### 3.2 축의 이동 — 채팅에서 에이전트·MCP로

2026년 이 카테고리의 메이저 발표는 예외 없이 에이전트/MCP 중심이다: Prisma AIRS 2.0 Agent Security, Netskope Agentic Broker(MCP 트랜잭션 전량 통제), Cisco AI BOM + MCP Catalog, Microsoft Agent 365.

- **OWASP Top 10 for Agentic Applications**가 2025-12-09 정식 발표 — shadow agents, agent identity, tool misuse 중심
- **MCP Tool Poisoning이 "새로운 프롬프트 인젝션"** — 공격자가 tool 메타데이터에 명령을 숨기고 에이전트는 읽지만 사용자는 못 본다. MCPTox 벤치마크(45개 실제 MCP 서버 · 353개 정품 툴)에서 인기 에이전트의 공격 성공률이 60%를 넘고 최고 72%
- Microsoft가 2026-06-30 공식 블로그로 오염된 MCP tool description이 에이전트의 데이터 유출을 유발한다고 경고

이것이 본 프로젝트가 **tool-call 층을 제품 축으로 채택**한 시장적 근거다([기획서 §1.3](기획서.md#13-차별화-축)).

### 3.3 인터셉션 3방식 비교

| 방식 | 장점 | 한계 | 대표 |
|---|---|---|---|
| **브라우저 확장** | 배포 빠름, SSL 인터셉션·인증서 배포 불필요, 암호화 이전 평문 접근, DOM 컨텍스트 확보 | 네이티브 앱·CLI·비관리 기기 미커버 | Harmonic, Nightfall, LayerX, Prompt Security |
| **인라인 네트워크 프록시(SSE/SASE)** | 전 트래픽 커버, 기존 SSE 투자 재활용 | 대규모 SSL 인터셉션 필요(인증서 배포, 피닝 회피, TLS 1.3/QUIC), PoP 인프라 | Zscaler, Netskope, Cato, PANW |
| **API / 게이트웨이 / SDK** | 사내 개발 앱·에이전트에 정밀 통제, 저지연, 컨텍스트 완비 | 직원의 SaaS LLM 직접 사용은 못 봄 | Bedrock Guardrails, Model Armor, Kong, LiteLLM, F5 |

메이저 벤더는 전부 하이브리드로 수렴 중이다(Palo Alto는 Network + API + Browser + AI Gateway + MCP 5개 동시 제공). **솔로 프로젝트가 고를 수 있는 것은 세 번째뿐이며, 그 선택과 그로 인해 못 보는 것을 명시하는 것이 정직한 설계다** — [ARCHITECTURE §2](ARCHITECTURE.md#2-인터셉션-지점-결정).

### 3.4 참고 수치

- Zscaler ThreatLabz: 2025년 ChatGPT 단일 앱에서만 DLP 정책 위반 4억 1,000만 건 이상 [벤더 주장]
- Fortinet: 엔터프라이즈 genAI 앱의 98%가 미승인 [벤더 주장]
- 레이턴시 예산 관측치: 게이트웨이 자체 오버헤드 ~11μs @5,000RPS [TrueFoundry 주장] · 소형 BERT 분류기 ~50ms · 목적 특화 guard 모델 ~29ms · LLM-as-judge 5~8.6초. **업계 권장: 입력 레일 p50 < 100ms, 출력 레일 p50 < 150ms, p50 200ms 초과 시 가드레일이 제품 병목**

---

## §4. 시장 갭 — 본 프로젝트의 좌표

국내외 조사에서 반복 확인된 다섯 개의 빈칸이다. 오른쪽 두 열이 본 프로젝트의 범위 결정 근거다.

| # | 갭 | 근거 | 본 프로젝트 | 위치 |
|---|---|---|---|---|
| ① | **한국 고유 식별자 PII 커버리지** — 주민번호·전화번호는 다들 하지만 사업자등록번호·법인등록번호·외국인등록번호·건강보험증번호·운전면허(지역코드)·여권을 체계적으로 다룬다고 명시한 곳이 드물다. Presidio는 **기본 영어 전용**이고 OpenAI Privacy Filter의 엔티티 8종에도 한국 식별자가 없다 | Presidio 공식 문서가 "기본 설정에는 영어용 recognizer와 모델만 포함"이라 명시 | ✅ **제품 축 A** | [기획서 §1.3](기획서.md#13-차별화-축) |
| ② | **공개 한국어 인젝션 코퍼스 부재** — 2025 정보보호학회 논문(30만+ 프롬프트)은 미공개, `korean-guardrail-dataset`의 인젝션 파트는 5K 수준 번역본. 한글 자모분해·초성체·로마자·한자혼용·존댓말 사회공학 등 한국어 고유 우회 벡터는 체계적으로 다뤄진 적이 없다 | Prompt Guard 2·ProtectAI DeBERTa는 한국어 미지원. Kanana Safeguard-Prompt조차 F1 0.844로 개선 여지 | ✅ **평가 축 B** | [기획서 §3](기획서.md#3-벤치마크평가셋-구축) |
| ③ | **tool-call / MCP 층 DLP** — 어떤 OSS 게이트웨이도 tool call *인자* 안의 PII나, tool 결과로 들어온 비신뢰 데이터가 다음 tool call로 유출되는 경로를 추적하지 않는다. LiteLLM MCP Guardrails·NeMo tool-calling rail은 호출 허용 여부만 본다 | 국내에서는 윈스만 근접. OWASP Agentic Top 10은 2025-12에야 나옴 | ✅ **제품 축 C** | [ARCHITECTURE §5](ARCHITECTURE.md#5-tool-call-dlp) |
| ④ | **한국어 가역 마스킹** — 조사 결합(`홍길동이` → `[PERSON_1]이`) 처리가 한국어 특유의 난제인데 이걸 푸는 OSS가 없다. 레퍼런스였던 LLM Guard의 Anonymize/Deanonymize는 2026-07-09 아카이브됐고 한국어 품질도 낮았다 | — | ✅ **제품 축 A에 포함** | [ARCHITECTURE §3](ARCHITECTURE.md#3-가역-마스킹-볼트) |
| ⑤ | **오탐률(FPR) 공개 부재** — 국내 8개 제품 중 오탐률을 수치로 공개한 곳이 0. 공개 비교 벤치마크도 없어 비교 자체가 불가능 | §2.2 매트릭스 · 과탐이 곧 우회(섀도우 AI)를 유발한다는 InjecGuard(arXiv:2410.22770)의 지적 | ✅ **헤드라인 지표로 승격** | [기획서 §2](기획서.md#2-목표-및-성공-지표) |

미대응으로 남기는 갭(정직하게 기록): Shadow AI 앱 카탈로그, 멀티모달 유출 경로(OCR·HWP/HWPX 파싱), 출력물 워터마킹, 비용·토큰 거버넌스.

---

## §5. 규제 앵커 → 기능 번역

국내 시장의 수요 동인이자, 감사 로그 스키마의 `regulatory_tags[]` 설계 근거다([ARCHITECTURE §6](ARCHITECTURE.md#6-감사-로그-스키마)).

| 규제·지침 | 시점 | 핵심 요구 | 게이트웨이 기능으로의 번역 |
|---|---|---|---|
| **인공지능 기본법** | **2026-01-22 전면 시행** (계도 최소 1년) | 투명성 의무, 안전성 의무, 고영향 AI 사업자 책무, AI 영향평가 | **감사 로그 보관이 법적 요구사항으로 승격.** 요청별 모델·정책·판정 이력의 불변 기록 + 설명 가능한 판정 근거(어떤 recognizer가 왜 걸었는가) |
| **개인정보보호위원회 「생성형 AI 개발·활용을 위한 개인정보 처리 안내서」** | 2025-08-06 | 생애주기 4단계(목적 설정 / 전략 수립 / 학습·개발 / 시스템 적용·관리)별 개인정보 처리 기준·안전조치 | **입력 단계 PII 통제 + 가명처리**의 직접 근거. 정책 템플릿을 4단계 조항에 1:1 매핑 |
| **금융분야 AI 가이드라인 개정안** | **2026-06-22 시행** | 7대 원칙(거버넌스·적법성·보완성·신뢰성·금융안정·신의성실·**보안**). 금감원 AI 리스크관리 프레임워크 + 금융보안원 AI 보안 가이드 동시 배포 | 금융권 도입 시 필수 통제 항목의 체크리스트화 |
| **금융권 생성형 AI 활용 지원방안** | 2025~ | 망분리 완화 + 비조치의견서. 침해사고 대응기관 보안성 평가 통과 SaaS만 이용 가능 | 온프렘 배포 가능성이 사실상의 진입 요건 |
| **N2SF (국가망보안체계)** | 2026 「국가 사이버보안 기본 지침」 개정으로 공식화 | 정보서비스 모델 8종에 "생성형 AI 활용" 포함. KISA 2026 도입 지원 45억, 6개 컨소시엄 | 망분리 완화 = 공공기관이 외부 AI에 접근 → **프롬프트 통제 레이어가 필수 전제**. 탐지 모델의 완전 로컬 구동이 요건 |
| **국정원 / 과기정통부 AI 보안 가이드라인** | 2023-06 최초 → 개정판 + 「AI 보안 가이드북」(15개 위협 · 30개 대응) | 공공(국정원, N2SF 연계) / 민간(과기정통부) 이원 | 위협 분류를 정책 카테고리로 사용 |

> **설계에의 직접 함의**: N2SF와 금융권 요건이 겹치는 지점이 "탐지 엔진 자체가 어디서 도는가"다. 국내 벤더 대부분이 "LLM 기반 탐지"라고만 하고 그 LLM의 위치를 밝히지 않는다. 본 프로젝트는 **탐지는 전부 로컬, 생성만 외부**라는 분리를 명시적 설계 원칙으로 세운다([ARCHITECTURE §7](ARCHITECTURE.md#7-온디바이스--api-분리-원칙)).

---

## §6. 컴포넌트 스택 — 채택 후보와 라이선스

### 6.1 채택 후보

| 컴포넌트 | 라이선스 | 역할 | 근거 수치 (출처는 §7) |
|---|---|---|---|
| **LiteLLM proxy** | MIT | 게이트웨이 본체 | 100+ 프로바이더, 훅 4종(`pre_call`/`during_call`/`post_call`/`logging_only`), 커스텀 가드레일 클래스, ad-hoc Presidio recognizer 주입, MCP guardrail 문서화 |
| **Presidio** | MIT (Microsoft → data-privacy-stack 커뮤니티 이관) | PII 프레임워크 골격 | Recognizer/Anonymizer 추상화. **기본 영어 전용 — 한국어 recognizer는 직접 작성해야 함** |
| **kanana-safeguard-prompt-2.1b** (카카오) | **Apache 2.0** | 인젝션·프롬프트 리킹 입력 레일 | 한/영 F1 **0.844** (PromptGuard 0.751 / deepset 0.638 / GPT-4o 0.804). **단일 토큰 출력** → 1토큰 생성만으로 판정, 레이턴시 유리 |
| **kanana-safeguard-8b** | **Apache 2.0** | 비동기 유해성 라벨링 | 한국어 유해 F1 **0.946** (LlamaGuard3 0.540 / ShieldGemma 0.477 / GPT-4o zero-shot 0.763) |
| **kanana-safeguard-siren-8b** | **Apache 2.0** | 비동기 법적·개인정보 리스크 라벨링 | F1 **0.926** (LlamaGuard3 0.692 / GPT-4o 0.862). ⚠️ 분류만 하고 **엔티티 span 추출은 안 함** → 마스킹에 단독 사용 불가 |
| **FrameByFrame/privacy-filter-korean** | Apache 2.0 (openai/privacy-filter 상속) | PII span 추출 NER | KDPII span F1 **0.848**. base 모델은 **active 50M 파라미터**(sparse MoE) → **CPU 실용 가능**. ⚠️ 태그셋 9종에 **한국 고유 식별자 없음** |
| **Qwen3Guard-Stream 0.6B** | Apache 2.0 | (스트레치) 토큰 단위 스트리밍 판정 | 119개 언어. 스트리밍 중 실시간 차단이 가능한 유일한 계열. ⚠️ 한국어 개별 수치 미공개 → 자체 검증 필요 |
| **LLM Guard** | MIT, **2026-07-09 아카이브** | 설계 청사진 (코드 채택 아님) | 입력 15종 + 출력 20종 스캐너 taxonomy, Anonymize→LLM→Deanonymize 볼트 패턴 |

**라이선스 조합 판정**: LiteLLM(MIT) + Presidio(MIT) + Kanana 3종(Apache 2.0) + privacy-filter(Apache 2.0) + Qwen3Guard(Apache 2.0) — **전부 상용 배포까지 제약 없음.** 브리프의 "on-device 모델 라이선스 확인" 요구를 이 표가 충족한다.

### 6.2 검토 후 기각

| 대상 | 기각 사유 |
|---|---|
| **Llama Guard 3/4, Prompt Guard 2 (86M/22M)** | 공식 지원 언어에 **한국어 없음**. 22M은 다국어 사전학습 자체가 없음(모델카드 명시). 추가로 Llama Community License는 MAU 7억 제한 + 브랜딩 의무 |
| **ShieldGemma / ShieldGemma 2** | 영어 중심 4개 카테고리. ShieldGemma 2는 이미지 전용이라 텍스트 DLP와 무관. (구 프로젝트도 3연속 결격으로 기각한 바 있다) |
| **protectai/deberta-v3-*-prompt-injection-v2** | 모델카드가 영어 전용 + 탈옥 미탐지를 명시 |
| **Cloudflare AI Gateway** | 프롬프트가 Cloudflare로 나간다 — **데이터 유출 방지 제품이 데이터를 외부로 보내는 논리적 모순.** 국내 실사에서 탈락 |
| **Kong AI Gateway** | OSS 티어가 `ai-proxy` + 정규식 `ai-prompt-guard`뿐. 시맨틱 가드·PII sanitizer·커스텀 가드레일은 전부 Enterprise |
| **smilegate-ai/kor_unsmile** | 비영리·학술 목적 배포 → 상용 학습 불가 |
| **ai4privacy/pii-masking-*** | 커스텀 학술 라이선스, 상용은 별도 계약 필요 |
| **OPA/Rego (초기 도입)** | 솔로 5주 범위에 운영 부담 과잉. 단 스키마는 Rego 컴파일 가능한 형태로 설계 — [ARCHITECTURE §6](ARCHITECTURE.md#6-감사-로그-스키마) |
| **NeMo Guardrails** | Apache 2.0에 활발(v0.23.0, 2026-07)하고 HF classifier rail로 Kanana를 꽂을 수 있으나, Colang DSL 학습 비용이 솔로 5주에 부담. **v2 후보로 기록** |

### 6.3 한국어 데이터 자산

| 자산 | 규모 | 라이선스 | 판정 |
|---|---|---|---|
| **KDPII** (한국어 대화형 PII 비식별화 데이터셋) | ~53,778건 | 확인 필요 (Zenodo/IEEE) | 한국어 PII의 사실상 유일한 공개 학습셋. **라이선스 확인이 W1 선결 과제** |
| `korean-guardrail-dataset` | PII 53,778 + 합성 55,940 / 모더레이션 280,000+ / **인젝션·탈옥 5,059(대부분 번역본)** | 혼재 (CC-BY-SA-4.0, CC-BY-4.0, Apache-2.0, 일부 불명) | 인젝션 파트는 held-out 검증용으로만 |
| K-MHaS, KoBBQ, KorNAT, KoTox, K-HATERS 등 | — | 개별 상이 | `awesome-korean-safety`에 정리. 본 프로젝트 직접 사용은 없음 |
| **자체 KoTIP v0.1** (구 프로젝트 산출) | 공격 60건 (2 시나리오 × 5 스타일 × 6 난독화) | 자체 | KoTIP-v2의 **시드**. 난독화 뮤테이터 6종 코드 포함 |

---

## §7. 학술 앵커

| 참조 | 본 프로젝트에서의 역할 |
|---|---|
| **OWASP Top 10 for LLM Applications 2025** ([한국어판 PDF](https://owasp.org/www-chapter-seoul/assets/files/TopTenForLLM-ko_KR.pdf), OWASP Seoul) | 정책 카테고리를 LLM01(프롬프트 인젝션)·LLM02(민감정보 노출)에 매핑. 국내 문서에 한국어판이 있다는 점이 실무상 유리 |
| **OWASP Top 10 for Agentic Applications** (2025-12-09) | tool-call 층 위협 분류의 규범적 근거 |
| **CaMeL — Defeating Prompt Injections by Design** (arXiv:2503.18813) | **tool-call DLP의 이론적 뼈대.** Privileged LLM이 계획을 세우고 Quarantined LLM이 비신뢰 데이터를 처리하며, 인터프리터가 provenance를 추적해 tool call 직전에 capability 정책을 강제. 본 프로젝트는 이를 게이트웨이 레벨에서 경량 구현(신뢰 라벨 + taint 전파) |
| **Spotlighting** (arXiv:2403.14720, Microsoft) | 구분자·데이터마킹으로 신뢰/비신뢰 텍스트를 모델에게 구분해줌. 게이트웨이가 프롬프트를 재작성하는 방식으로 비용 없이 구현 가능 |
| **InjecGuard** (arXiv:2410.22770) / **PromptShield** (arXiv:2501.15145) | **과탐(over-defense) 문제를 정량화.** 사내 게이트웨이에서 FP는 곧 우회를 부른다 → 차단 대신 마스킹→경고→승인 3단 에스컬레이션을 기본값으로 삼는 근거 |
| **Bypassing LLM Guardrails: An Empirical Analysis of Evasion Attacks** (arXiv:2504.11168) | 문자 삽입·적대적 회피로 DeBERTa 계열 탐지기를 뚫음. **한국어판 우회 벡터(자모분해·초성체)는 아직 아무도 체계적으로 다루지 않았다** → 갭 ② |
| **SafeGPT** (arXiv:2601.06366) | 입력 탐지·리댁션 + 출력 모더레이션 + HITL의 양면 가드레일. **가장 가까운 선행연구 — 반드시 인용하고 차별점(한국어 특화 + tool-call 층)을 명시할 것** |
| **Benchmarking Open-Source Safety Guard Models** (arXiv:2605.28830, ICLR 2026 workshop) | 14개 OSS 가드 모델 / 79,331 샘플. Qwen Guard 4B가 recall 83.97%로 최고인 반면 대형 모델(Llama Guard 12B, gpt-oss-safeguard 20B)은 최대 75%를 놓침. 결론: **"안전 응용에서는 recall이 결정적이며, 크기 ≠ 성능"** — 소형 특화 모델 캐스케이드의 근거 |
| **MCPTox** (arXiv:2508.14925) / **MCP Tool Poisoning** (OWASP, Invariant Labs) | tool description 포이즈닝의 공격 성공률 근거 (60~72%) |
| **KDPII** (IEEE Access, Li Fei·강예지·박서윤 외) | 한국어 PII 평가셋의 근거 |

---

## §8. 출처

### SecureBridge · 국내
- [SecureBridge 제품 페이지](https://ahnlabcloudmate.com/ai-and-data/securebridge) · [안랩 출시 보도자료](https://company.ahnlab.com/kr/news/press_release_view.do?seqPressRelease=10147) · [데일리시큐 — 우수 정보보호기술 지정 상세](https://www.dailysecu.com/news/articleView.html?idxno=207393) · [ZDNet — 우수기술 선정](https://zdnet.co.kr/view/?no=20260701205949) · [아이티데일리 — Guardian Architecture·PEP/PDP·Shadow AI 1,200종](http://www.itdaily.kr/news/articleView.html?idxno=240020) · [아시아경제 — 디지털서비스 등록](https://www.asiae.co.kr/article/2026020210103636672) · [바이라인 — 안랩클라우드메이트 전략 인터뷰](https://byline.network/2025/07/17-483/)
- [바이라인 — 국내 생성형 AI 보안 솔루션 지형](https://byline.network/2025/08/25-468/) · [Fasoo AI-R DLP](https://www.fasoo.ai/products/fasoo-ai-r-dlp) · [Fasoo AI-R DHC](https://www.fasoo.com/products/ai-r-dhc) · [바이라인 — 지란지교 시큐어 AI 오피스](https://byline.network/2026/07/14-603/) · [소만사 생성형AI 보안대책 PDF](https://www.somansa.com/wp-content/uploads/2024/08/ai_20240823.pdf) · [컴트루 Sphinx AI](https://cometrue.ai/index.php/sphinxai/) · [데일리시큐 — 윈스 SNIPER AIVAX](https://www.dailysecu.com/news/articleView.html?idxno=205695) · [데일리시큐 — 이로운앤컴퍼니 SAIFE X](https://www.dailysecu.com/news/articleView.html?idxno=203985) · [보안뉴스 — 세이퍼존 AI-DLP v10](https://m.boannews.com/html/detail.html?idx=141997) · [디지털데일리 — DLP 오탐 문제](https://www.ddaily.co.kr/page/view/2026060613174346273)

### 글로벌
- [PANW — Protect AI 인수 완료](https://www.paloaltonetworks.com/company/press/2025/palo-alto-networks-completes-acquisition-of-protect-ai) · [PANW — Portkey 인수 완료](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents) · [SentinelOne — Prompt Security 인수](https://www.sentinelone.com/press/sentinelone-to-acquire-prompt-security-to-advance-genai-security/) · [Cato — Aim 인수](https://www.catonetworks.com/news/cato-acquires-aim-security-to-extend-sase-leadership-and-secure-enterprise-ai-transformation/) · [F5 — CalypsoAI](https://www.f5.com/company/blog/what-are-ai-guardrails) · [Check Point — Lakera 인수](https://www.checkpoint.com/press-releases/check-point-acquires-lakera-to-deliver-end-to-end-ai-security-for-enterprises/) · [Zscaler — SPLX 인수](https://www.zscaler.com/press/zscaler-secures-enterprise-ai-lifecycle-acquisition-innovative-ai-security-pioneer-splx)
- [Zscaler AI Security](https://www.zscaler.com/products-and-solutions/ai-security) · [Netskope One AI Security](https://investors.netskope.com/news-releases/news-release-details/netskope-unveils-netskope-one-ai-security-delivering-high) · [Cisco — 에이전틱 시대 보안](https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m02/cisco-redefines-security-for-the-agentic-era.html) · [Microsoft — MCP 툴 포이즈닝 경고](https://www.microsoft.com/en-us/security/blog/2026/06/30/securing-ai-agents-ai-tools-move-from-reading-acting/) · [WitnessAI 시리즈B](https://witness.ai/resources/witnessai-raises-58-million-for-global-expansion-and-announces-new-ways-to-secure-ai-agents/) · [TrueFoundry — 가드레일 벤치마크](https://www.truefoundry.com/blog/benchmarking-llm-guardrail-providers)

### 컴포넌트·모델
- [LiteLLM Guardrails Quick Start](https://docs.litellm.ai/docs/proxy/guardrails/quick_start) · [LiteLLM Custom Guardrail](https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail) · [LiteLLM Presidio PII Masking](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2) · [LiteLLM MCP Guardrails](https://docs.litellm.ai/docs/mcp_guardrail)
- [Presidio 커뮤니티 이관 공지](https://presidio.dataprivacystack.org/project_transition/) · [Presidio 지원 언어 문서](https://presidio.dataprivacystack.org/analyzer/languages/) · [data-privacy-stack/presidio](https://github.com/data-privacy-stack/presidio)
- [kanana-safeguard-prompt-2.1b](https://huggingface.co/kakaocorp/kanana-safeguard-prompt-2.1b) · [kanana-safeguard-8b](https://huggingface.co/kakaocorp/kanana-safeguard-8b) · [kanana-safeguard-siren-8b](https://huggingface.co/kakaocorp/kanana-safeguard-siren-8b) · [카카오 기술블로그](https://tech.kakao.com/posts/705)
- [openai/privacy-filter](https://huggingface.co/openai/privacy-filter) · [FrameByFrame/privacy-filter-korean](https://huggingface.co/FrameByFrame/privacy-filter-korean) · [QwenLM/Qwen3Guard](https://github.com/QwenLM/Qwen3Guard) · [protectai/llm-guard (아카이브)](https://github.com/protectai/llm-guard) · [NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails)
- [meta-llama/Llama-Guard-4-12B](https://huggingface.co/meta-llama/Llama-Guard-4-12B) · [Llama-Prompt-Guard-2-86M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) · [google/shieldgemma-2b](https://huggingface.co/google/shieldgemma-2b)

### 규제·학술
- [개인정보위 생성형 AI 개인정보 처리 안내서](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS074&mCode=C020010000&nttId=11410) · [금융위 — 금융분야 AI 가이드라인 개정안](https://www.fsc.go.kr/no010101/87142) · [금융위 — 금융권 생성형 AI 활용 지원방안 PDF](https://www.fsc.go.kr/comm/getFile?srvcId=BBSTY1&upperNo=83594&fileTy=ATTACH&fileNo=7) · [보안뉴스 — 국정원 vs 과기정통부 AI 보안 지침 비교](https://m.boannews.com/html/detail.html?idx=140867) · [데일리시큐 — KISA N2SF 도입 지원사업](https://www.dailysecu.com/news/articleView.html?idxno=207197)
- [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) · [OWASP LLM Top 10 2025 한국어판 PDF](https://owasp.org/www-chapter-seoul/assets/files/TopTenForLLM-ko_KR.pdf) · [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) · [OWASP MCP Tool Poisoning](https://owasp.org/www-community/attacks/MCP_Tool_Poisoning)
- [CaMeL arXiv:2503.18813](https://arxiv.org/abs/2503.18813) · [Spotlighting arXiv:2403.14720](https://arxiv.org/abs/2403.14720) · [InjecGuard arXiv:2410.22770](https://arxiv.org/pdf/2410.22770) · [PromptShield arXiv:2501.15145](https://arxiv.org/pdf/2501.15145) · [Guardrail 우회 arXiv:2504.11168](https://arxiv.org/pdf/2504.11168) · [SafeGPT arXiv:2601.06366](https://arxiv.org/abs/2601.06366) · [OSS Safety Guard 벤치마킹 arXiv:2605.28830](https://arxiv.org/html/2605.28830) · [MCPTox arXiv:2508.14925](https://arxiv.org/pdf/2508.14925)
- [KDPII (Zenodo)](https://zenodo.org/records/10968609) · [KDPII (IEEE Access)](https://ieeexplore.ieee.org/document/10681073/) · [awesome-korean-safety](https://github.com/marker-inc-korea/awesome-korean-safety) · [korean-guardrail-dataset](https://github.com/skan0779/korean-guardrail-dataset)
