# 제출 안내 — 무엇을 어떻게 내고, 받는 쪽은 무엇을 하면 되나

> 이 문서 하나로 **패키징 → 검증 → 재현**이 끝나도록 썼다.
> 모든 수치는 이 저장소에서 실행해 확인한 값이다.

---

## 1. 제출물 구성

| 산출물 | 경로 | 비고 |
|---|---|---|
| 발표자료 (편집 가능·정본) | `report/발표자료.pptx` | 18장 · 발표자 노트 포함 |
| 발표자료 (배포용) | `report/발표자료.pdf` | Keynote 네이티브 내보내기 |
| 발표 대본 | `report/발표자료_대본.md` | 슬라이드별 3단(표준/압축/용어) + 실행 런북 부록 |
| 프로젝트 코드 | 저장소 전체 | 아래 §2 참조 |
| 데이터·모델 | Hugging Face | [cycloevan/safeagent-kr](https://huggingface.co/cycloevan/safeagent-kr) · §4 |

**발표자료 3종은 모두 `scripts/pptx/gen_pptx.mjs` 하나에서 나온다.** 손으로 편집한 부분이
없으므로 수치가 갱신되면 재생성만 하면 된다(§5).

---

## 2. 코드 패키징 — 무엇을 넣고 무엇을 뺄까

`git archive` 로 추적 파일만 뽑으면 **8.5 MB** 다. 이대로 제출하면 된다.

```bash
git archive --format=zip --prefix=safeagent-kr/ HEAD -o safeagent-kr.zip
unzip -l safeagent-kr.zip | tail -1        # 크기 확인
```

| 디렉터리 | 크기 | 제출 | 이유 |
|---|---:|---|---|
| `src` `eval` `app` `scripts` `config` `tests` | 10.7 MB | ✅ | 구현 본체 |
| `data/kotip/v0.1` | 1.5 MB | ✅ | 평가셋 — 이게 있어야 재현된다 |
| `results` | 924 KB | ✅ | 발표 수치의 출처. 없으면 검증 불가 |
| `docs` `report` | 5.6 MB | ✅ | 아키텍처 그림 · 발표 산출물 |
| `train/artifacts` | 1.6 MB | ❌ | HF 에서 받는다 (`python -m scripts.fetch_rail`) |
| `models/` | 682 MB | ❌ | **재배포 제한** — §4 |
| `.venv` | 763 MB | ❌ | `requirements.txt` 로 재생성 |
| `.git` | 111 MB | △ | 이력이 필요 없으면 zip 으로 제출 |

> `.git` 이 큰 이유는 발표자료 pptx/pdf 를 반복 재생성해 커밋했기 때문이다
> (pptx 60 MB + pdf 50 MB 상당). 이력이 필요 없다면 위 `git archive` zip 을 쓴다.

---

## 3. 받는 쪽 재현 절차 — API 키 없이 동작

```bash
python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

./.venv/bin/python -m pytest -q                 # 137 passed
./.venv/bin/python -m scripts.run_demo          # 무방어=유출 / 게이트=차단
./.venv/bin/python -m app.trace_view            # → report/trace_view.html
```

**클린 체크아웃에서 검증했다** — 추적 파일만 풀어 실행한 결과:

```
✅ 데모 결과 정합: 무방어=유출 · 게이트=차단 (headline 재현)
137 passed
```

레일 학습 아티팩트(`train/artifacts/`)가 없어도 돈다 — `heuristic` 으로 폴백하며 화면에
경고가 뜬다. 슬라이드와 같은 레일 점수(0.038)를 보려면 §4 의 `fetch_rail` 을 먼저 돌린다.

브라우저 데모는 `docs/USAGE.md §3-3`, 촬영 절차는 `report/demo.md` 를 따른다.

---

## 4. 데이터·모델 — Hugging Face

### 올리는 것 (`python -m scripts.push_hf`)

```bash
python -m scripts.push_hf --dry-run     # 계획만 확인
HF_TOKEN=hf_... python -m scripts.push_hf
```

| 대상 | 크기 | 라이선스 |
|---|---:|---|
| KoTIP-Bench (attacks·utility) | 74 KB | CC-BY-4.0 · 자체 구축 |
| `nemotron_rail.*` (train/dev/test) | 1.4 MB | CC-BY-4.0 파생 (출처표기) |
| `ko-agentguard-lr.joblib` | 1.6 MB | 자체 학습 |
| `e5-lr-head.joblib` | 2.3 KB | e5=MIT + 자체 head |
| `DATA_CARD.md` | 3.5 KB | — |

### ★올리지 않는 것★ — 재배포 제한

| 대상 | 크기 | 이유 |
|---|---:|---|
| `r6-pii-ner/` | 258 MB | `klue/roberta-small` 파생. 베이스 모델 카드에 라이선스 메타가 없고 상위 KLUE 프로젝트가 CC-BY-SA-4.0 |
| `r7-kmhas/` | 423 MB | `klue/roberta-base` 파생 — 위와 동일 |

**이 둘은 수치만 보고하고 가중치는 로컬 보관한다.** `models/gpu_trained/MANIFEST.md` 에 같은
판단이 적혀 있고, `scripts/push_hf.py` 의 `DENY_PREFIXES` 가 코드로 막는다. 심사에서 모델을
요청하면 학습 스크립트(`eval/train_pii.py`·`train_harm.py`)와 `report/GPU_RUNBOOK.md` 로
재학습 경로를 제시한다.

### 받는 쪽

```bash
python -m scripts.fetch_rail        # HF → train/artifacts/ (SHA256 검증)
```

---

## 5. 발표자료 재생성 (수치가 바뀌었을 때)

**순서를 지켜야 한다** — 그림이 `results/` 에서 값을 읽고, 덱이 그림을 읽는다.

```bash
./.venv/bin/python -m eval.ablation            # results/ 재집계
./.venv/bin/python -m scripts.run_demo         # 데모 트레이스
PYTHONPATH=. python3 -m scripts.gen_slide_figs # 차트
PYTHONPATH=. python3 -m scripts.gen_arch_stack # 아키텍처
PYTHONPATH=. python3 -m scripts.gen_workflow   # 워크플로
./.venv/bin/python -m app.trace_view && PYTHONPATH=. ./.venv/bin/python -m scripts.gen_trace_shot
cd scripts/pptx && npm run build && cd ../..   # pptx + 대본
./.venv/bin/python -m scripts.export_deck_pdf  # pdf
./.venv/bin/python -m pytest -q                # 회귀
```

빌드가 **자동으로 막는 것**: 그림 텍스트가 카드를 넘치면 실패, 슬라이드에 `★` 가 남으면
테스트 실패, OOXML 스키마 위반이면 테스트 실패, PPT 노트와 대본이 갈리면 테스트 실패.

---

## 6. 제출 전 확인

```bash
git status --short                              # 비어야 한다
./.venv/bin/python -m pytest -q                 # 137 passed
git ls-files -z | xargs -0 grep -lIE "sk-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{30,}"   # 출력 없어야 한다
```

- `.env` 는 `.gitignore` 에 있다. 이력에 `.env` 커밋이 두 건 보이지만(`011300f` 추가 →
  `639f048` 삭제) 내용은 **빈 줄 하나**였고, 전체 blob 을 `sk-`·`AIza`·`hf_`·`ghp_`
  패턴으로 스캔한 결과 **실제 키는 이력 어디에도 없다**.
- `models/` `train/artifacts/` `.venv` 는 gitignore 처리돼 있다.
- 발표자료 3종이 최신 `results/` 로 재생성된 상태인지 §5 로 확인한다.
