# DATA_CARD — SafeAgent-KR

데이터셋·모델의 출처·라이선스·용도를 한 곳에 정리한다. 코드 라이선스는 [LICENSE](LICENSE)(MIT).

## 1. 이 프로젝트가 만든/배포하는 산출물

| 산출물 | 내용 | 라이선스 | 재배포 |
|---|---|---|---|
| **KoTIP-Bench** (`data/kotip/v0.1/attacks·utility.*.jsonl`) | 한국어 도구채널 인젝션 평가셋(5style×6obf×2scenario + benign) — 자체 구축 | **CC-BY-4.0** | 가능 |
| `data/kotip/v0.1/nemotron_rail.*.jsonl` | 인젝션 레일 학습/평가 텍스트·라벨 — Nemotron 파생 | **CC-BY-4.0**(파생) | 가능(출처표기) |
| `models/gpu_trained/models_final/e5-lr-head.joblib` | e5-small(동결)+LR head | e5=MIT, head=자체 | 가능 |
| `models/gpu_trained/models_final/ko-agentguard-lr.joblib` | TF-IDF+LR CPU 레일 | 자체 | 가능 |
| HF 공개: [cycloevan/safeagent-kr](https://huggingface.co/cycloevan/safeagent-kr) | 위 안전세트 | CC-BY-4.0 | — |

> **결정성 주의:** 커밋된 KoTIP-Bench 는 **동결(frozen)**이며 `results/runs.jsonl` 과 정합한다.
> `eval/build_dataset.py` 의 고객 배정은 결정적 해시(sha256)이나, 재빌드하면 커밋본과 다를 수 있으므로 재빌드하지 않는다.

## 2. 평가/학습에 사용한 외부 공개 데이터셋

| 데이터셋 | 용도 | 라이선스 | 파생물 재배포 |
|---|---|---|---|
| `nvidia/Nemotron-RL-Agentic-Indirect-Prompt-Injection-v1` (1,272) | 레일 학습+평가 | CC-BY-4.0 | 가능 |
| `Fujitsu-FRE/MAPS` ASB/Korean (400) | 평가 | CC-BY-4.0 | 가능 |
| `ai4privacy/pii-masking-openpii-1.5m` (`ko`) | R6 PII NER 학습(오프라인, 20k 서브샘플) | CC-BY-4.0 | 가능 |
| `jeanlee/kmhas_korean_hate_speech` (109,692) | R7 유해 학습(오프라인) | CC-BY-SA-4.0 | 가능(SA) |
| `deepset/prompt-injections` (662) | 평가 스모크 | Apache-2.0 | 가능 |
| `heegyu/XSTest_ko` (450) | R10 과차단(**미실행**) | 미표기 | **불가 — 수치만** |

## 3. 비교/추론에 사용한 모델

| 모델 | 용도 | 라이선스 |
|---|---|---|
| `kanana-safeguard-prompt-2.1b` / `-8b` | 한국어 인젝션 baseline·레일(채널갭 실측) | Apache-2.0 |
| `google/shieldgemma-2b` | EN-KO 유해 갭 실측(log-only) | Gemma(비-OSI, gated) |
| `klue/roberta-small` / `-base` | R6/R7 파인튜닝 베이스 | **개별 HF 모델 카드엔 라이선스 메타 없음**; 상위 [KLUE 프로젝트](https://github.com/KLUE-benchmark/KLUE)는 **CC-BY-SA-4.0** 명시. 파생 가중치 재배포는 학습데이터 라이선스·출처표기·**SA(동일조건)** 확인 전까지 **보류**(현재는 수치만 보고) |
| `intfloat/multilingual-e5-small` | e5 레일 인코더(동결) | MIT |

## 4. 민감 데이터·PII 취급 정책

- 런타임: 요청 스코프 가역 PII 볼트(RegexVault 5종: EMAIL/RRN/CARD/PHONE/ACCOUNT). 모델은 토큰만 본다.
- **로그에 원본 PII·API 키를 기록하지 않는다**(`src/agent/log.py`, `policy.yaml log_mappings:false`, `scripts/check_env.py`는 키 접두/길이도 미기록).
- 평가 PII 는 전부 합성(Faker `ko_KR` + 템플릿). 실제 개인정보 없음.
- 학습 가중치 중 `klue` 파생(R6/R7)은 라이선스 미표기로 공개 재배포에서 제외(로컬 보관·수치 보고만).
