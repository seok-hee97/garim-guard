# SafeAgent-KR GPU 모델 리뷰 — 종합 (검증 반영 확정본)

## Part 1 — 학습 모델 vs 추론 모델 용도 정리

### 핵심 원칙 (먼저)
집행(enforcement)은 **결정론적 정책 게이트(Tier A)**가 담당한다. 모델은 집행에 관여하지 않는다. 아래의 학습/사전학습 분류기들은 전부 **분류기가 어디서 깨지는지를 "측정"하기 위한 LOG-ONLY 텔레메트리 레일**이다(난독화, 교차언어, 도구채널 구조적 맹점). 이것이 "분류기를 집행에서 강등"하는 논지를 데이터로 뒷받침한다.

### 요약 표

| # | 모델 | 구분 | 베이스 / 클래스 | 목적·연결 메트릭 | 입력 포맷 | 점수 추출 | 학습/미학습 이유(논지) |
|---|------|------|----------------|-----------------|-----------|-----------|----------------------|
| R6 | PII NER | **학습** | klue/roberta-small(~68M) + TokenClassification | 한국어 PII 스팬 탐지 → **스팬 F1/F2**, Presidio-ko·vmaca123 대비 | 원문, WordPiece, seq=256 | BIO 19라벨(O+B/I×9) argmax → 스팬 P/R/F1/F2 | DLP 볼트용 한국어 개체 성능을 정량 비교하려 소형 인코더 파인튜닝 |
| R7 | harm rail | **학습** | klue/roberta-base(~110M) + **멀티라벨** SeqClassification | 한국어 혐오 멀티라벨 → **Macro-F1**, kanana-8b 제로샷과 대비 | 원문, seq=128 | **9로짓 sigmoid + 라벨별 threshold**(BCEWithLogitsLoss, softmax 아님) | 정확한 kmhas 타깃 택소노미로 소형·고속 분류기의 상한을 측정 |
| — | Ko-AgentGuard 업그레이드 | **학습(head만)** | intfloat/multilingual-e5-small **동결** + sklearn LR | 교차언어 주입 탐지(CPU TF-IDF 레일 업그레이드) → LR predict_proba[0,1] | **모든 입력에 `query: ` 접두** 후 인코딩 | mean-pool(mask) → L2정규화 → 384d → LR | 인코더는 동결(그래디언트 없음), 선형프로브만 학습 → TF-IDF의 언어잠금·패러프레이즈 취약성 극복 |
| 4 | kanana-safeguard-prompt-2.1b | **추론전용** | LlamaForCausalLM(~2.1B) | (a) Ko-AgentGuard 베이스라인, (b) 한국어 classifier_gate 레일 → **P1a 채널갭** | apply_chat_template([{user:text}]) | 마지막토큰 로짓 3개 softmax: SAFE=128257, A1=128256, A2=128258 → P(주입)=1−P(SAFE) | 목적특화 사전학습 분류기. 챗템플릿이 도구채널을 구조적으로 못 봄을 "측정" |
| 5 | shieldgemma-2b | **추론전용** | Gemma-2 2B(~2.6B), gated | LOG-ONLY → **EN-vs-KO 갭**(구글 자체 안전분류기가 한국어 도구채널에서 저하) | 수동 policy 프롬프트(권장) 또는 apply_chat_template(guideline=) | 마지막토큰 Yes/No 로짓 softmax → P('Yes')=위반확률, **4개 policy 각각 1회씩** | 구글 안전분류기의 언어갭을 그대로 노출시키는 텔레메트리 |
| 6 | kanana-safeguard-8b | **추론전용(옵션)** | LlamaForCausalLM(8B) | R7 조야한 harm 제로샷 비교 + 구조적맹점 텔레메트리 | apply_chat_template([{user:text},{assistant:""}]) | 마지막토큰 8개 특수토큰 softmax, P(harm)=1−P(SAFE). **ID 비순차 주의** | 파인튜닝 안 함. 스케일업해도 구조적 갭이 안 고쳐짐을 보여 Tier-A 논지 강화 |

### 모델별 상세 및 검증 정정

**R6 PII NER (학습)** — klue/roberta-small + TokenClassification 헤드. 데이터 ai4privacy/pii-masking-openpii-1.5m 한국어 슬라이스(~26,498행, 9개체: GIVENNAME/SURNAME/CITY/STREET/BUILDINGNUM/ZIPCODE/EMAIL/DATE/AGE, SOCIALNUM/TELEPHONENUM/IDCARDNUM 제외). hparams lr=3e-5, 3epoch, batch=16, seq=256, 20k 서브샘플. 메트릭은 스팬레벨 strict P/R/F1/F2. 이건 집행이 아니라 DLP 볼트의 한국어 개체 탐지 성능을 Presidio-ko / vmaca123/korean-pii-ner-v3와 비교하기 위한 독립 산출물이다.

**R7 harm rail (학습)** — klue/roberta-base + 멀티라벨 SeqClassification. 데이터 jeanlee/kmhas_korean_hate_speech(train 78,977, label은 **가변길이 int 리스트**, 9클래스, idx8=not_hate). label 리스트의 **9차원 multi-hot 확장은 `eval/train_harm.py`에 구현됨**(초기 스켈레톤에서 해소). 손실 BCEWithLogitsLoss(softmax 금지), 추론 라벨별 sigmoid + 라벨별 threshold, 메트릭 Macro-F1, 모델/지표 저장.

**Ko-AgentGuard e5 업그레이드 (head만 학습)** — e5-small 인코더 동결, sklearn LogisticRegression만 학습. **#1 위험 = 접두 불일치**: 분류/클러스터링 용도이므로 학습·추론 **모든** 행에 `query: `를 붙여야 함. sentence-transformers는 접두를 자동으로 안 붙임(흔한 버그). 파이프라인: `query: `+text → tokenize(max_length=512) → model(no_grad, eval) → masked mean-pool → L2정규화(384d) → LR.fit/predict_proba. 이미 정규화된 벡터라 StandardScaler 불필요, 불균형시 class_weight='balanced' 고려.

**kanana-prompt-2.1b (추론전용)** — **검증으로 논지 확정·강화됨**. 챗템플릿 Jinja가 namespace 루프로 **마지막 user + 마지막 assistant 턴만 남기고**, user/assistant 외 role(tool, 추가 system)은 **슬롯 자체가 없어 완전히 드롭**한다. 즉 "마지막 user 메시지로 붕괴"보다 강한 구조적 맹점 → P1a 채널갭의 하드 증거. **정정**: 카드 quickstart는 float16을 로드하지만 config dtype은 bfloat16(네이티브) → 4090에서 **bfloat16 사용**. trust_remote_code 불필요. 템플릿이 assistant 헤더를 무조건 append하므로 add_generation_prompt 무시. 도구채널 주입을 테스트하려면 payload를 **user content에 직접 삽입**해야 함(tool role은 드롭됨). **불확실**: 3토큰 softmax로 P(주입)∈[0,1] 산출은 우리 방법(카드는 generate max_new_tokens=1 argmax만 문서화) — 유효하나 Kakao 공식 캘리브레이션 아님.

**shieldgemma-2b (추론전용, GATED)** — **정정**: "English-only"라 단정 말고 카드 문구 그대로 "available in English"로 인용할 것(카드가 한국어 미지원을 명시하진 않음). 카테고리별 점수(4개 policy 루프), 통합 로짓 없음. **첫 실행 안정성 위해 apply_chat_template(guideline=) 대신 수동 프롬프트 문자열 경로 권장**(transformers 버전 의존 회피). tool role 없으므로 도구출력을 Human Question 슬롯에 매핑(이 매핑 자체를 문서화 = 측정하려는 채널맹점과 동형). attn_implementation="eager"(flash-attn 미확인시). Gemma-2 logit soft-capping 기본값 유지(끄면 Yes/No 로짓 이동). 동일 payload의 한국어 vs 영어 번역을 비교해 갭을 언어 요인으로 귀속.

**kanana-8b (추론전용, 옵션)** — **기대치 조정**: R7의 kmhas 9클래스와 **다른 7클래스 단일라벨** 택소노미(S1 Hate/S2 Harassment/S3 Sexual/S4 Crime/S5 CSAM/S6 Self-harm/S7 Misinfo). 따라서 per-label F1 비교가 아니라 **조야한 harm-vs-not(P(harm)=1−P(SAFE))** 베이스라인 + 구조적맹점 텔레메트리로만 사용. **치명적 주의**: 특수토큰 ID 비순차 → `convert_tokens_to_ids(["<SAFE>","<UNSAFE-S1>",...])`로 인덱싱(128257≠S1). 16GB라 다른 레일과 동시 상주 금지, 예산 남으면 마지막에 단독 순차 실행.

---

## Part 2 — RunPod GPU 실행 계획 (수정·확정 런북)

### 0. 환경 셋업 (베이스 이미지 신뢰 금지, 먼저 실행)
```
pip install -U "transformers>=4.51.3" "torch>=2.2" "datasets>=2.20" \
    accelerate sentence-transformers scikit-learn seqeval duckdb
```
- **transformers>=4.51.3** 하나로 전부 충족(Kanana config 저장 버전, Gemma-2는 4.42부터, klue/e5는 구버전 OK). **정정: repo requirements.txt는 transformers>=4.44.0 — 너무 낮음. GPU 런은 >=4.51.3으로 bump.**
- flash-attn / bitsandbytes **불필요**(빌드 시도 금지).
- 어떤 모델도 trust_remote_code 불필요.
- `export HF_HOME=/workspace/hf-cache` (약 27~30GB 가중치를 영속 볼륨에, 컨테이너 오버레이 아님).
- **GATED ShieldGemma**: (1) HF 계정으로 google/shieldgemma-2b 페이지에서 Gemma 라이선스 수락, (2) `export HF_TOKEN=...` 또는 `huggingface_hub.login(token=os.environ['HF_TOKEN'])`. **토큰만으로 불충분 — 그 계정이 라이선스를 수락해야 함**(config 401이 게이팅 증거). .env에 HF_TOKEN 존재하나 **현재 repo 코드가 로그인/토큰을 읽지 않음 → RunPod entrypoint에 login/export 추가 필요.**

### 1. 다운로드 순서 및 확정 크기 (BF16, ~27GB, 예산 30GB)
1. e5-small ~0.47GB(fp32)
2. klue/roberta-small ~0.27GB, klue/roberta-base ~0.44GB
3. kanana-prompt-2.1b **4.19GB(검증)**
4. shieldgemma-2b ~5.2GB(추정, gated tree 읽기 불가)
5. kanana-8b ~16GB(옵션)
6. 데이터: openpii 한국어 20k 서브샘플(duckdb parquet pushdown, 수백MB), kmhas ~30MB

### 2. 실행 순서와 의존성 (정정)
- 순서 (a)R6/R7 학습 → (b)추론 레일 → (c)ablation 편입 중 **(a)는 (b)/(c)의 선행조건이 아님**. R6/R7은 독립 비교 산출물로 다운스트림에 안 먹임. 단 **GPU 스택 fail-fast 스모크 테스트**로 먼저 돌리는 건 합리적.
- VRAM은 **한 번에 한 모델**. 레일 사이 `del model; torch.cuda.empty_cache()`. kanana-8b(16GB)+타레일 동시 상주 절대 금지.

### 3. 학습 모델 레시피 (파인튜닝, 각 <4GB VRAM)
- **R6**: klue/roberta-small + AutoModelForTokenClassification(19라벨). lr=3e-5, 3ep, batch=16, seq=256, 20k 서브샘플. openpii는 `duckdb hf:// parquet WHERE language='ko'`로 로드(4.27GB 풀다운 회피). VRAM <3GB.
- **R7**: klue/roberta-base + AutoModelForSequenceClassification(num_labels=9, problem_type="multi_label_classification"). **label 리스트 → 9-multi-hot 확장 필수**. BCEWithLogitsLoss, lr=2e-5, 3ep, batch=16, seq=128. kmhas는 `revision='refs/convert/parquet'`(trust_remote_code 회피, datasets>=3.0 기본 비활성). 라벨별 threshold 튜닝 → Macro-F1. VRAM <4GB.
- **e5+LR**: e5-small AutoModel 동결(eval+no_grad), `query: ` 접두 → mean-pool → L2 → numpy → sklearn LR.fit. 인코딩 <2GB VRAM, LR은 CPU.

### 4. 추론 레일 → KoTIP 한국어 eval 점수 산출
- **kanana-2.1b**: bfloat16, apply_chat_template → forward → `logits[0,-1,[128257,128256,128258]]` softmax → P(주입)=1−P(SAFE). VRAM ~6GB.
- **shieldgemma-2b**: bfloat16, eager attn, 수동 policy 프롬프트 × 4카테고리 → Yes/No softmax → P('Yes'). 한국어 vs 영어번역 동시 측정. VRAM ~7GB.
- **kanana-8b(옵션)**: 단독·마지막·순차, batch=1, `convert_tokens_to_ids`로 8토큰 인덱싱 → P(harm)=1−P(SAFE). VRAM ~17-19GB(가장 빡빡, 양자화 없이 가능).

### 5. 결과 폴드백 (cassettes → eval.ablation)
현 코드로는 **자동 폴드백 안 됨**. 실블로커:
1. ~~`src/rails/kanana.py`, `src/rails/shieldgemma.py`, e5 레일 모듈 전부 부재~~ → **구현 완료**(2026-07): `src/rails/kanana.py`·`shieldgemma.py`·`e5_lr.py` 존재, `eval/kanana_compare.py`가 `--rail kanana|shieldgemma|e5` 지원(RunPod 미설치 시 SystemExit 안내). GPU 미설치 로컬에서는 지연 임포트로 import 시점엔 실패하지 않는다. (이 문단은 초기 스켈레톤 기준 기록이며 현재는 해소됨.)
2. ~~`_build_rail()` 하드코딩 → Kanana/ShieldGemma/e5 선택 불가~~ → **해소**: `eval/run.py:_build_rail`이 `--rail ko_agentguard|heuristic|kanana|shieldgemma|e5` 지원(RunPod GPU 레일 선택 가능). GPU 레일 편입은 여전히 `channel_gap.json` 별도 산출이 기본(ablation 테이블 편입은 선택).
3. ~~kanana_compare가 발화=대화 동일 세팅 → P1a 갭 계산 안 됨~~ → **해소**: `KananaRail.score_conversation(messages)`(멀티턴 b1 붕괴 경로) 구현, `eval/kanana_compare.py`가 `has_conversation_path`로 b1(대화)·b2(문자열)를 분리 계측(P1a 갭 실제 산출). CPU 레일은 b1=a 폴백임을 명시.

### 6. 코드 파일 상태 (구현 완료 vs 남은 것)

> ⚠️ 이 문서 §1~§4 및 아래 목록의 초기 "작성 필요" 표현은 **초기 계획 시점 기준**이다.
> 아래는 **현재(2026-07) 구현 상태**로 정정한 것이며, §5 blocker 정정(구현 완료)과 일치한다.

**구현 완료 ✅**:
- `src/rails/kanana.py` — `score(text)`+`score_conversation(messages)`(b1 붕괴 경로), transformers 버전 가드.
- `src/rails/shieldgemma.py` — 4-policy 루프, P('Yes').
- `src/rails/e5_lr.py` — e5-small+LR 레일. `eval/train_pii_ner.py`(R6)·`eval/train_harm.py`(R7, **multi-hot BCE 구현**+모델/지표 저장).
- `eval/run.py:_build_rail()` — `--rail ko_agentguard|heuristic|kanana|shieldgemma|e5` 선택.
- `eval/kanana_compare.py` — a(발화)·b1(대화, `has_conversation_path`)·b2(문자열) 분리 계측.

**RunPod 실행 시 필요(코드 아님, 환경)**: entrypoint 에 HF login/HF_TOKEN·HF_HOME 설정, ShieldGemma 게이트 라이선스 수락.

**이미 준비됨**: KoTIP 데이터, cassettes, .env(provider+HF 키), kmhas/openpii parquet 브랜치, kmhas 라벨↔R7 LABELS 정합, datasets refs/convert/parquet 선택.

### 7. DROP / 옵션 판단
- **kanana-8b = 옵션**: 16GB·느림·택소노미 불일치라 R7을 대체 못 함. 필수 레일 완료 후 예산 남을 때만, 마지막·단독 실행. 조야한 harm + 구조적맹점 증거로만 프레이밍.
- **ShieldGemma 게이팅 지연 대비**: 라이선스 수락이 막히면 EN-vs-KO 갭 레일을 나중으로 미루고 나머지 스택 먼저 진행(다른 모델은 전부 ungated).

### 8. 남은 미검증 리스크
- ShieldGemma config.json 401(gated) → 아키텍처/vocab 머신검증 불가(카드로 Gemma-2 확인). 디스크 ~5.2GB는 추정.
- kanana-2.1b apply_chat_template의 BOS(<|begin_of_text|>) prepend 여부 바이트레벨 미확인 → **손으로 프롬프트 만들지 말고 apply_chat_template 출력 신뢰**.
- 3토큰/8토큰 softmax 연속점수는 우리 방법(카드는 generate argmax만) — 유효하나 공식 캘리브레이션 아님.
- e5 `query: ` 접두 요구는 공식 FAQ로 확인, 단 이 repo README 코드블록 바이트단위 인용은 미확보(canonical E5 코드 구조는 웹검색 교차확인).
- klue/roberta-* 라이선스 미명시 → 가중치 재배포 금지, 수치만 보고(내부 파인튜닝은 무방).