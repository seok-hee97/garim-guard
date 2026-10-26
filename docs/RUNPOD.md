# RunPod 실행 절차

로컬(M1 / 16GB)에서 못 하는 것만 여기서 한다 — **교사 라벨링**과 **R1/R2 학습**이다.
왜 못 하는지는 실측으로 확인했다.

| | 로컬 | 이유 |
|---|---|---|
| kanana 2.1b 추론 | ✅ | 4.19GB, MPS 로 돈다 |
| gemma-4-E2B 추론 | ❌ | 10.28GB 를 16GB 에서 돌리면 스왑 — 4행에 10분 초과 |
| Qwen3Guard-**Gen** 0.6B | ✅ | Stream 을 대체했다 — 아래 참조. 1.4GB |
| R1/R2 gradient 학습 | ❌ | MPS 가 matmul 에서 죽는다. CUDA 가 필요하다 |

## ★ 올리는 것은 저장소뿐이다

코퍼스도 모델도 올리지 않는다. 둘 다 **재생성·재다운로드가 결정적**이기 때문이다.

예외가 하나 있고, 그래서 그것은 **저장소에 넣었다** — deepset 한국어 번역본
(`train/translations/`). LLM 번역은 재실행해도 비트 단위로 같지 않으므로, 결과 자체를
저장소에 두어야 이 원칙이 유지된다. 공개 CC-BY-4.0 데이터의 번역이라 개인정보가 없고
고지는 NOTICE 에 있다.

- 자산 18종은 `train/sources.yaml`·`train/models.yaml` 이 revision 을 고정한다.
  `download_assets.py --execute` 가 같은 것을 받는다.
- 합성 코퍼스는 시드 결정적이다. **재생성본이 로컬본과 비트 단위로 같은 것을 확인했다**
  (아래 해시). 그래서 원격에서 만들고 해시만 대조하면 동일성이 증명된다.

이 방식의 이득이 하나 더 있다. 합성 코퍼스에는 **체크섬을 통과하는 주민등록번호**가 들어
있다(`--ack-sensitive-synthetic-identifiers` 게이트가 그것 때문에 있다). 재생성 방식이면
그 값들이 **네트워크를 타지 않는다.**

### 대조용 해시 (로컬 기준, 2026-10-26)

`runpod_pipeline.py` 가 3·5c 단계에서 이 값을 출력한다.

```
15bca7d41d9df1ab9cd15826a21316c46b64ef9b47a7c14814d7d0a3cad56994  data/merged/pii-train.jsonl
c5f09f32fd61e4e1c538f0c74bd5e2272646732d2e90a5002b46d0c21b38a57a  data/merged/pii-validation.jsonl
5877a7d5c76689cfd1533daa1b32f19a587c4f4612633999977fc12f3d8e0840  data/corpus/attack-teacher-input.jsonl
34f9d7ac891fe1fb96476345bff8818d1b81899bf0768ae868c2f05dcaa4e48c  data/corpus/t4-test.jsonl
1cd1d529f5ab6b4abd2a1d18770fe5f1c1921ee176f4b0bafca12bf84bef40db  data/corpus/t4-prime-test.jsonl
```

2단계(`prepare_corpora.py`) 산출 16개도 2026-10-26 원자산에서 다시 만들어 로컬본과 **바이트
단위로 같은 것**을 확인했다.

(이전 판의 `attack-train/validation.jsonl` 해시는 합성 1,238행짜리 옛 봉인 입력이라 뺐다.
공격 학습 입력은 이제 7단계 교사 합의 산출물이다. PII 두 값은 3단계에 평가 차단 목록이
들어가며 바뀌었다 — 아래 "리허설이 찾은 것".)

⚠️ 이 값이 안 맞으면 **학습을 시작하지 않는다.** 원격이 다른 데이터를 보고 있다는 뜻이고,
그때 나오는 지표는 로컬에서 잰 어떤 것과도 비교할 수 없다.

## 환경

- **Python 3.11** — `requires-python = ">=3.11,<3.12"` 이다. 3.12 이미지를 고르면 `uv sync`
  가 거부한다.
- **드라이버가 CUDA 13 을 지원하는 pod** — `uv.lock` 의 torch 2.13.0 은 `manylinux_2_28_x86_64`
  휠이고 CUDA 13 런타임(`nvidia-*-cu13`)을 끌고 온다. pod 의 `nvidia-smi` 가 `CUDA Version`
  13.0 이상을 보여야 한다. RunPod 생성 화면의 CUDA 버전 필터로 고른다. 아니면 torch 가 GPU 를
  못 보고, `runpod_pipeline.py` 의 0단계(CUDA 확인)가 바로 멈춘다.
- **디스크 60GB 이상** — 고정 자산 약 17GB(교사 3종 15GB · 원천 데이터 2.5GB) + venv(torch·CUDA
  라이브러리) + 코퍼스·체크포인트·ONNX.
- `HF_TOKEN` 이 필요하다(gated 아닌 저장소도 rate limit 때문에 있는 편이 낫다).
- ⚠️ **기본 브랜치(`main`)는 구 프로젝트다.** 반드시 작업 브랜치를 받는다(2026-10-26 푸시됨).
- ⚠️ **저장소는 비공개다.** pod 에서 clone 하려면 인증이 필요하다. 이 저장소 하나에 Contents
  읽기 권한만 준 fine-grained 토큰을 만들어 `GITHUB_TOKEN` 으로 넣고, clone 직후 원격 URL 에서
  토큰을 지운다(남겨 두면 `.git/config` 에 평문으로 남는다).

```
pip install uv
export GITHUB_TOKEN=...      # 읽기 전용, 이 저장소만
git clone -b replan/genai-dlp-gateway \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/seok-hee97/nipa-google-project.git"
cd nipa-google-project
git remote set-url origin https://github.com/seok-hee97/nipa-google-project.git
uv sync --extra train
export HF_TOKEN=...
nvidia-smi        # CUDA Version >= 13.0 인지 확인
```

## 순서

```
1  python train/download_assets.py --execute
2  python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers --polyguard-limit 20000
3~10  python train/runpod_pipeline.py --device cuda            ← 학습 dry-run 까지
      python train/runpod_pipeline.py --device cuda --train    ← 3-seed 학습까지
11 python train/export_l1.py  →  python train/evaluate_l1.py  →  python train/benchmark_l1.py
```

`runpod_pipeline.py` 가 단계의 **유일한 출처**다. 안에서 하는 일:

| 단계 | 내용 |
|---|---|
| 3 | PII 병합 — **최종 평가셋 지문을 학습에서 뺀다**. 해시 출력 → 위 표와 대조 |
| 5·5a | 생성기 라벨 분리(사이드카 보관), deepset 번역 적용(`train/translations/`), 외부 이진 gold |
| 5b·5c | 공격 교사 풀 — 평가셋 지문과 **PII 학습 문장**을 뺀다. train+validation 을 한 입력으로(19,195행) |
| 6 | 교사 3종 — `run_teacher.py` 가 이어 돌기를 지원하므로 끊기면 같은 명령을 다시 부른다 |
| 6b | 일치도·JAILBREAK 독립성 — 동의율 ≥ 0.95 면 멈춘다(`--accept-rubber-stamp` 로만 진행) |
| 7 | 합의(`--drop-review`) — 합의된 행 전체에서 **성분 단위**로 검증 10% 를 떼어 낸다 |
| 8·8b | 학습 봉인 → 평가 봉인(학습 봉인 해시 재검증 + 지문 교집합 ∅) |
| 9·10 | preflight training → 학습 dry-run → (`--train`) 학습 |

### 11 — 학습 뒤: seed 선택 · 내보내기 · 평가 · 회수

seed 선택은 자동이 아니다. `train/artifacts/l1/three-seed-report.json` 의 seed 별
`best_joint_recall_hmean`(검증 PII char-recall 과 공격 macro-recall 의 조화평균)이 가장 높은
seed 를 고르고, **고른 이유를 모델 카드에 적는다**(`train/MODEL_CARD_TEMPLATE.md`).

```
S=<고른 seed>
python train/export_l1.py --encoder-dir models/pinned/l1_ax_encoder \
  --checkpoint train/artifacts/l1/seed-$S/joint_l1.pt --output-dir train/artifacts/l1-onnx
python train/evaluate_l1.py --model-dir train/artifacts/l1-onnx \
  --baseline-model-file garim_l1.fp32.onnx --output train/artifacts/l1-eval.json \
  $(for f in pii-synthetic-test attack-synthetic-test t4-test t4-prime-test \
      attack-external-gold-test kdpii-test klue_eval-test bc_pii_augmented_negative-test; \
    do printf -- '--input data/corpus/%s.jsonl ' $f; done)
python train/benchmark_l1.py --model-dir train/artifacts/l1-onnx --output train/artifacts/l1-bench.json
```

`evaluate_l1.py` 의 `--input` 은 8b 에서 봉인한 여덟 파일 **전부**여야 한다 — 봉인과 다르면 거부한다.

**학습이 GPU 메모리 부족(OOM)으로 죽으면** `train/training.yaml` 의 `batch_size` 를 줄이고 같은
명령을 다시 부른다. 교사 단계는 이어 돌기라 이미 끝난 행을 건너뛰고, 봉인(8·8b)은 바뀐 설정으로
다시 만들어진다. batch 32 × 512토큰 fp32 의 VRAM 은 재지 못했다(이 Mac 은 swap 이 거의 차서 RSS
측정이 무의미했다) — 첫 epoch 시작 직후 `nvidia-smi` 로 확인한다.
`benchmark_l1.py` 의 지연은 pod CPU 값이라 §7.1.3 의 노트북 기준과 다르다. 지연은 로컬에서
다시 잰다.

**pod 를 끄기 전에 가져올 것** — pod 디스크는 남지 않을 수 있다.

| 경로 | 무엇 |
|---|---|
| `train/artifacts/l1/` | seed 별 체크포인트·학습 보고서, `three-seed-report.json` |
| `train/artifacts/l1-onnx/` | FP32·QInt8 ONNX, `labels.json`, 토크나이저 — 런타임 `--l1-model-dir` 로 바로 쓴다 |
| `train/artifacts/l1-eval.json` · `l1-bench.json` | 평가·지연 |
| `data/training-input-manifest.json` · `data/evaluation-input-manifest.json` | 무엇으로 학습·평가했는가(봉인) |
| `data/corpus/teacher-*.jsonl` · `data/corpus/resolved/` | 교사 라벨 원본·합의·폐기 감사(재학습 시 교사를 다시 안 돌려도 된다) |
| `data/teacher_agreement.json` | 6b 일치도·JAILBREAK 독립성 |

⚠️ `data/` 에는 체크섬을 통과하는 합성 주민등록번호가 있다. 가져온 것도 Git 밖에 둔다.

### ★ 먼저 로컬에서 리허설한다 — GPU 없이 2b~10 배선 전체

```
python train/runpod_pipeline.py --rehearse
```

교사 3종 대신 **교사 대역**(형식·ID 범위·투표 구조만 같고 라벨은 지어낸 것,
`prompt_version: rehearsal-standin`)을 만들고 나머지를 실제 CLI 로 돈다. 교사·합의·봉인
산출물은 Git 밖 `train/artifacts/rehearsal/` 에만 생기고 학습은 dry-run 이다. M1 에서 수 분,
모델은 A.X 인코더 dry-run 하나만 올린다. **RunPod 을 켜기 전에 매번 돌린다.**

### 리허설이 찾은 것 — 교사 실행 뒤 단계의 결함 다섯 (2026-10-26)

전부 GPU 교사를 다 돌린 **뒤에야** 드러날 자리였다.

| # | 단계 | 증상 | 원인 → 조치 |
|---|---|---|---|
| 1 | 6b | 기본 `--gold` 파일 없음 · `extra=17,787` | 사이드카가 분할별 두 파일로 갈렸고(25a3528), 하네스가 교사 산출물이 gold 와 **정확히** 같은 ID 이기를 요구했다 → 기본값 두 파일, gold ⊆ 교사 산출물·교사끼리 같은 ID 집합 요구 |
| 2 | 7 | `template 'benign-076' crosses dataset splits` | 버킷을 `group or template` 하나로 정해 group 여럿에 걸친 template 이 갈렸다(합성 300개 전부) → union-find 성분 단위 분할 |
| 3 | 7 | 검증 분할의 PolyGuard 0행 | PolyGuardMix `group_id` 가 하위 데이터셋 이름이라 17,273행이 group 둘로 뭉쳤다 — 해시가 반대로 떨어졌다면 공격 데이터 84% 가 검증으로 빠졌다 → 행 단위 group |
| 4 | 8 | `fingerprint crosses validation/train` | `네, 맞습니다.` 류가 KDPII(양 분할)와 PolyGuard 에 같이 있었다 → 공격 풀에서 PII 학습 문장 제거(`--blocked-other-task`) |
| 5 | 8b | `evaluation … overlap training` | PII 병합에 평가 차단 목록이 없었다 — KDPII test 일반 발화, KLUE train·validation 의 라벨까지 같은 문장 → 3단계에서 학습 쪽을 제거. **봉인 규칙은 그대로 엄격하다** |

> 2026-10-26 로컬 상태: 1~5c 완료, 리허설 통과. **로컬에서 교사 둘(kanana·Qwen)만 먼저
> 돌릴 이유가 없다** — 정족수 2표에서 kanana 는 NONE/INJECTION/SYSTEM_LEAK, Qwen 은
> JAILBREAK 만 투표하므로 Gemma 없이는 어떤 행도 합의되지 않는다. M1 에서 kanana 는 약
> 45행/분(다른 작업과 CPU·메모리를 나눠 쓴 상태의 실측)이라 19k 에 7시간 안팎이다.

### 공격 교사 풀 — PolyGuardMix 샘플링

어댑터가 언어를 `"ko"` 로 걸러서 **PolyGuard 한국어를 한 행도 수확하지 못하고 있었다**
(상류는 `"Korean"` 이라고 적는다). 92,740행이 통째로 버려졌고, 그것이 공격 학습 풀을
1,238행으로 묶어 둔 원인이었다.

전량을 쓰면 교사 3종 추론이 278k forward 라 §6 의 R2 추정(~1,920)을 크게 넘는다.
**20,000행을 결정적으로 샘플링**한다 — 레코드 id 의 안정 해시 순위로 뽑으므로 parquet
glob 순서가 달라져도 같은 집합이 나오고, 원천이 커져도 대부분 유지된다.

    PolyGuardMix 한국어   92,710  (평가 지문 중복 30 제거 후)
    → 20,000 샘플         merge 에서 중복 2,727 제거
    → 최종 교사 풀        18,511  (PolyGuard 17,273 + 합성 1,238)

    PII 108,588 : 공격 18,511 = 5.9:1   (고치기 전 88:1)
    pii_per_attack=4 기준 업샘플 1.5배   (이전 21.7배)

**`pii_per_attack` 결정이 이것으로 해소됐다.** 공격 1행을 5에폭에 109번 보던 것이
5~6번이 된다.

### 8b — 평가 봉인은 왜 따로인가

학습 입력만 봉인하면 "무엇으로 학습했는가" 는 고정되지만 **"무엇으로 평가했는가" 는
열려 있다.** 평가 코퍼스를 나중에 바꾸면 지표가 조용히 달라지고, 더 나쁘게는 학습에
쓴 행이 평가로 새어 들어와도 아무도 모른다.

`finalize_evaluation_inputs.py` 는 봉인하기 전에 **학습 매니페스트의 해시를 먼저
재검증**하고(schema 2 · 내용 drift · 레코드 수 drift), 그 다음 **정규화 지문 교집합이
∅ 인지** 확인한다. 낡거나 손댄 학습 매니페스트로는 통과할 수 없다.

실제로 강제되는 것을 확인했다 — 학습 20행을 `split=test` 로 바꿔 평가로 봉인하려 하니
위반 id 를 짚어 거부했다.

    ValueError: evaluation text fingerprints overlap training inputs:
        ['syn-attack-0056', 'syn-attack-0099', ...]

봉인 대상은 넷이다 — 합성 PII test · 합성 공격 test · **T4 2,800** · **T4′ 400**
(총 5,872행).

⚠️ **8번(봉인)이 5~7번 뒤에 온다.** 교사 라벨이 붙은 코퍼스를 봉인해야 한다. 봉인은
`train/training.yaml` 까지 덮으므로 하이퍼파라미터를 바꾸면 **재봉인**이다 — 바꾸고
안 하면 `training input path drift` 로 막힌다.

## 교사 교체 — Stream → Gen (해소됨)

`Qwen/Qwen3Guard-Stream-0.6B` 의 원격 코드가 transformers 4.x 를 벤더링한 것이라 우리
pin(`>=5.5,<6`)에서 안 돈다. `config.pad_token_id` 를 채워 넘겼더니 8줄 뒤
`ROPE_INIT_FUNCTIONS['default']` 로 또 막혔고, 그 파일은 transformers 내부 14개 모듈을
임포트한다. 상류가 안 고쳤다(고정본 = 현재 main). **하드웨어 무관이라 여기서도 같다.**

뺄 수는 없었다 — `minimum_votes: 2` 인데 JAILBREAK 투표자가 gemma4 와 이 교사 둘뿐이라,
빼면 그 클래스가 정족수를 영영 못 채우고 전부 폐기된다.

**`Qwen/Qwen3Guard-Gen-0.6B` 로 교체했다.** 원격 코드가 없어 표준 클래스로 로드되고,
같은 계열·같은 택소노미라 `votes_for: [JAILBREAK]` 역할이 유지된다. 로컬에서 확인했다.

확률은 kanana 와 같은 **단일 토큰 로짓**으로 얻는다 — Gen 은 판정을 텍스트로 내므로
접두를 강제로 넣고 다음 토큰 분포를 읽는다. 후보 첫 토큰이 겹치지 않는 것을 구성 시점에
확인하고, 겹치면 실패한다.

### ⚠️ 반드시 함께 재야 하는 것 — JAILBREAK 동의율

§6.2.2 가 **Gen-0.6B 의 폴란드 FPR 17.17%** 를 명시했다(Bielik Guard 실사용 측정).
과탐이 심한 교사가 2표 중 1표를 맡으면 **거의 항상 동의해서 사실상 Gemma 단독 판정**이
된다 — 설계가 세 번째 교사를 넣어 막으려던 바로 그 상태다.

```
python -m bench.teacher_agreement \
  --teacher kanana=<...> --teacher gemma4=<...> --teacher qwen3guard_gen=<...> \
  --primary gemma4 --secondary qwen3guard_gen
```

**동의율 ≥ 0.95 면 교체 실패로 본다.** 두 번째 표가 정보를 더하지 않는다는 뜻이고, 그때는
4.x 격리 venv 로 되돌아간다. 같은 하네스가 **생성기 라벨과 교사 라벨의 일치도**도 낸다 —
자작 공격 코퍼스를 외부 모델로 검증할 유일한 기회다.

## 학습 전 확인할 것 — 해소됨

이전 판은 여기에 `pii_per_attack` 결정을 남겨 뒀다. PolyGuard 어댑터를 고치면서
불균형이 88:1 → 5.9:1 이 되어 **결정할 것이 없어졌다.** 아래는 기록으로 남긴다.



```
PII 108,588 : 공격 1,238 = 88:1   (§6 추정은 54:1)
pii_per_attack=4 → 공격 1행을 에폭당 22번, 5에폭이면 110번
```

T4 는 평가 전용이라 이 비율을 바꾸지 않는다. 그대로 갈지, `pii_per_attack` 을 올릴지,
공격 학습 풀을 키울지는 **9번 preflight 통과 후 10번 전에** 정한다. 바꾸면 재봉인이다.
