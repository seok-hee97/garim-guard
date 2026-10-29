# 외부 모델 연결

> 기준: 0.1.5 (2026-10-29). `--l1-backend` · `--l2-backend` 는 0.1.3 부터 있다.

PyPI 패키지 `garim-guard` 에는 검사 로직과 학습 · 내보내기 보조 모듈(`garim.training`, `[train]` 필요)이 들어 있다. 모델을 고르고 학습하는 일은 패키지 밖에서 하고,
그 결과를 아래 네 자리 중 하나에 붙인다. 가림은 모델을 내려받지 않는다.

| 붙이는 것 | 방법 | CLI · 프록시 | 라이브러리 |
|---|---|---|---|
| 로컬 LLM 판정기(L2) | OpenAI 호환 서버 — `--l2-endpoint` · `--l2-model` | ✓ | `LocalChatL2Backend` |
| 가림 형식으로 학습한 L1 | ONNX 폴더 — `--l1-model-dir` | ✓ | `OnnxL1Backend` |
| 그 밖의 모델 · 사전(L1) | 파이썬 백엔드 — `--l1-backend 모듈:함수` | ✓ | `Inspector(l1_backend=...)` |
| 그 밖의 판정기(L2) | 파이썬 백엔드 — `--l2-backend 모듈:함수` | ✓ | `Inspector(l2_backend=...)` |

- L1 은 모든 모드에서, L2 는 `--mode thorough` 에서만 돈다.
- 붙인 모델이 실행 중에 실패하거나 시간 예산을 넘기면, L0 판정은 그대로 남고 `degraded=true` 와 사유가
  붙는다. 실행 중의 `sys.exit()` 도 실패로 본다. 시작할 때 실패하면(파일 없음 · import 오류 · `sys.exit()` ·
  계약 위반) 검사하지 않고 종료 코드 `4` 로 끝난다.
- 사번처럼 형식이 정해진 번호는 모델 없이 정책 YAML 의 `custom_patterns`(정규식)로 잡는다.

## 1. 로컬 LLM 을 판정기로 (L2)

OpenAI 호환 `/v1/chat/completions` 를 루프백에서 여는 서버면 된다. 루프백이 아닌 주소는 거부한다.

```bash
# Ollama — 기본 주소 127.0.0.1:11434
ollama pull <모델>
garim --mode thorough --l2-endpoint http://127.0.0.1:11434/v1 --l2-model <모델> check '이전 지시를 무시해'

# llama.cpp — README 의 Gemma 4 GGUF 예시
llama-server -m <파일>.gguf --host 127.0.0.1 --port 8081 --alias <이름> --jinja
garim --mode thorough --l2-endpoint http://127.0.0.1:8081/v1 --l2-model <이름> check '...'
```

- 프롬프트는 고정이다(`garim/detect/l2.py` 의 `GEMMA_POLICY_PROMPT`). 모델에게 네 범주
  (`NONE` · `INJECTION` · `JAILBREAK` · `SYSTEM_LEAK`) 중 하나만 출력하게 한다. 응답에서 범주를 찾지
  못하면 한 번 다시 묻고, 그래도 없으면 `degraded`(`l2_parse_error`)다.
- 잰 모델은 Gemma 4 E2B 뿐이다([L2_EVALUATION.md](L2_EVALUATION.md)). 다른 모델은 5절로 직접 잰다.

## 2. 가림 형식으로 학습한 L1 (ONNX 폴더)

가림의 학습 코드([TRAINING.md](TRAINING.md))가 내는 형식이다. 인코더는 `train/train_l1.py --encoder-dir`
로 바꿀 수 있다. 다만 학습 입력 · 설정 · 자산 manifest 를 봉인과 대조하므로 파이프라인
(`train/runpod_pipeline.py`)을 거쳐 돌린다. 자기 학습 코드로 같은 4헤드 모델을 만들었다면
`garim.training.export` 의 `export_joint_onnx` · `quantize_joint_onnx` · `write_l1_metadata` 로 ONNX 와
`labels.json` 을 만들고, 토크나이저를 `save_pretrained` 로 같은 폴더에 둔다(`train/export_l1.py` 가 하는 일).

| 파일 | 내용 |
|---|---|
| `garim_l1.int8.onnx` | 이름은 `--l1-model-file` 로 바꾼다 |
| `labels.json` | `pii_labels` — 모델 출력 순서대로 `O` 와 14개 개체의 `B-` · `I-` 쌍, 29개 |
| `tokenizer.json` · `tokenizer_config.json` | fast 토크나이저 — 원문 위치를 되찾는 데 offset 이 필요하다 |

| 그래프 | 이름 | 모양 | 뜻 |
|---|---|---|---|
| 입력 | `input_ids` · `attention_mask` | `[batch, seq]` int64 | 토크나이저 출력(`token_type_ids` 는 그래프에 있을 때만 넘긴다) |
| 입력 | `normalization_delta` | `[batch, 7]` float32 | 정규화 카운트의 `log1p` — `nfkc` · `zero_width` · `tag_chars` · `homoglyph` · `jamo_composed` · `hanja` · `b64_decoded` 순 |
| 출력 | `pii_logits` | `[batch, seq, 29]` | 토큰별 BIO 로짓 |
| 출력 | `attack_logits` | `[batch, 4]` | `NONE` · `INJECTION` · `JAILBREAK` · `SYSTEM_LEAK` 순 |
| 출력 | `romanized_logits` · `honorific_logits` | 원소 하나 | 로마자 우회 · 경어 사회공학 보조 헤드(시그모이드) |

개체 라벨 14개(괄호는 가림 코드, 가림 학습 코드의 출력 순서): `KR_RRN`(L-KR-01) · `KR_FRN`(L-KR-02) · `KR_PASSPORT` · `KR_DL` · `KR_BIZNO` ·
`KR_CORPNO` · `KR_NHI` · `KR_ACCOUNT`(L-KR-08) · `CARD`(L-CM-01) · `PHONE` · `EMAIL`(L-CM-03) ·
`PERSON`(L-CT-01) · `ADDRESS`(L-CT-02) · `ORG_ID`(L-CT-03).

## 3. 파이썬 백엔드 — L1

형식이 다른 모델(일반 NER, 사내 분류기)이나 사전 · 목록을 L1 자리에 붙인다. 계약은
`garim.detect.l1.L1Backend` 다.

```python
from garim.detect.l1 import L1Entity, L1Output

class MyBackend:
    name = "my-model"
    max_length = 512          # 선택 — 정책 limits.chunk_size 가 이보다 크면 시작하지 않는다

    def infer(self, text, *, normalization_delta, origin, content_type) -> L1Output:
        spans = ...           # text(정규화된 문자열) 기준 (start, end)
        return L1Output(entities=[L1Entity("PERSON", (start, end), score) for start, end, score in spans])

    def cancel_current(self, thread_id=None):   # 선택 — 시간 예산을 넘긴 추론을 멈춘다
        ...

def build():                  # 인자 없이 불린다 — 설정은 환경 변수 · 자기 설정 파일에서 읽는다
    return MyBackend()
```

```bash
garim --l1-backend ./my_backend.py:build check '...'     # 파일 경로
garim --l1-backend my_package.garim_plugin:build proxy --upstream https://api.openai.com/v1   # 설치된 모듈
```

지켜야 하는 것:

- **위치는 `text` 기준이다.** `text` 는 정규화를 거친 문자열이고, 가림이 원문 위치로 되돌린다.
- **라벨은 위 14개 중 하나다.** 그 밖의 라벨은 버린다.
- **점수는 0 ~ 1 이다.** 런타임 신뢰도는 점수 × 0.9(`KR_RRN` · `KR_FRN` 은 × 0.7)이고, 정책
  `thresholds.l1_pii`(기본 0.5) 이상이어야 채택된다. 확률이 없는 모델이면 1.0 을 준다.
- **공격 판정은 선택이다.** `attack_probabilities` 를 주면 네 범주가 모두 있어야 한다. 네 범주 중 최댓값이
  `NONE` 이 아니고 `thresholds.l1_atk`(기본 0.5) 이상이면 공격으로 본다. 주지 않으면 `NONE` 1.0 이다.
- **스레드 안전해야 한다.** 프록시는 요청마다 다른 스레드에서 같은 객체를 부른다. 보장하지 못하면 내부에서
  `threading.Lock` 으로 감싼다.
- **시간 예산이 있다.** `limits.l1_timeout_ms`(기본 1000ms)를 넘기면 그 청크는 L0 로만 판정되고
  `degraded`(`l1_timeout`)다. CPU 에서 느린 모델이면 정책에서 예산을 늘린다. 백엔드 하나에서 동시에 도는
  추론(예산을 넘겨 버려졌지만 아직 도는 것 포함)이 8개면 새 추론을 시작하지 않는다(`l1_busy`). `Inspector`
  하나는 한 번에 하나씩 부른다. `cancel_current` 를 구현하면 버려진 추론을 멈출 수 있다.
- **파일 경로 형식은 그 파일 하나만 올린다.** `sys.path` 를 건드리지 않으므로, 여러 파일로 된 백엔드는
  패키지로 만들어 설치(`pip install -e .`)하거나 `PYTHONPATH` 에 둔다.

## 4. 파이썬 백엔드 — L2

계약은 `garim.detect.l2.L2Backend` 다.

```python
from garim.detect.l2 import L2Judgement, L2ParseError

class MyJudge:
    name = "my-judge"

    def judge(self, text, *, origin) -> L2Judgement:
        label, score = ...    # NONE · INJECTION · JAILBREAK · SYSTEM_LEAK 중 하나
        return L2Judgement(label, score, "my-judge")    # 읽을 수 없는 출력이면 L2ParseError
```

- 공격 판정의 신뢰도는 `thresholds.l0`(기본 0.5)와 비교한다. 콘텐츠 유형별 가감이 붙는다(로그 +0.10).
- 한 청크를 정규화 스트림 셋(`norm` · `detensified` · `nospace`)으로 최대 세 번 부른다. 우회를 되돌린 각
  스트림을 따로 판정해야 탐지가 오른다는 측정을 따른 것이다(RESULTS §1).
- `L2ParseError` 는 `l2_parse_error`, 그 밖의 예외는 `l2_error:<예외 이름>` 으로 degraded 가 된다.

## 5. 붙인 모델 재기 — 소스 저장소의 `bench/`

배포 모델과 같은 셋 · 같은 판정 경로로 잰다. ONNX 폴더는 `--l1-model-dir`, 파이썬 백엔드는
`--l1-backend` 를 넘긴다.

```bash
# 개인정보 — L1 원출력 · 런타임 임계 · 제품 경로(Inspector) 세 단계
python -m bench.l1_product_path --l1-backend ./my_backend.py:build \
  --input data/corpus/kdpii-test.jsonl --output artifacts/my-pii.json

# 프롬프트 공격 — KoreanGuardrail(외부)
python -m bench.run_axis_b --benchmark <KoreanGuardrail 경로> --judge garim-l1 \
  --l1-backend ./my_backend.py:build --output artifacts/my-attack.json
```

평가 데이터를 받는 방법은 [TRAINING.md](TRAINING.md) 1 · 2절에 있다. L2 판정기는 1절의 서버를
`bench.run_axis_b --judge gemma4-l2 --l2-endpoint ... --l2-model ...` 으로 잰다.

## 6. 예시 — [`examples/backends/`](../examples/backends/)

| 파일 | 자리 | 내용 |
|---|---|---|
| `l1_name_list.py` | L1 | 고객 · 임직원 이름 목록을 성명으로 가린다. 의존성 없음 |
| `l1_hf_ner.py` | L1 | 직접 학습한 Hugging Face 토큰 분류(NER) 모델. 라벨 대응표만 맞춘다 |
| `l2_hf_classifier.py` | L2 | 직접 학습한 프롬프트 공격 분류기. LLM 서버 없이 판정기로 쓴다 |

세 파일 모두 `tests/test_backends.py` 가 CI 에서 계약을 확인한다. Hugging Face 예시는 모델 대신
가짜 파이프라인을 넣어 확인한다.

## 7. 경계

- 가림이 지키는 성질(검사 경로에서 외부 통신 없음 · 모델을 내려받지 않음)은 **가림 코드**의 것이다.
  붙인 백엔드가 무엇을 하는지는 그 코드가 정한다. [SECURITY.md](../SECURITY.md) 의 범위 밖이다.
- 붙인 모델의 라이선스는 사용자가 확인한다.
