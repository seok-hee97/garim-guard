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

- 자산 18종은 `train/sources.yaml`·`train/models.yaml` 이 revision 을 고정한다.
  `download_assets.py --execute` 가 같은 것을 받는다.
- 합성 코퍼스는 시드 결정적이다. **재생성본이 로컬본과 비트 단위로 같은 것을 확인했다**
  (아래 해시). 그래서 원격에서 만들고 해시만 대조하면 동일성이 증명된다.

이 방식의 이득이 하나 더 있다. 합성 코퍼스에는 **체크섬을 통과하는 주민등록번호**가 들어
있다(`--ack-sensitive-synthetic-identifiers` 게이트가 그것 때문에 있다). 재생성 방식이면
그 값들이 **네트워크를 타지 않는다.**

### 대조용 해시 (로컬 기준)

```
59bb0493a33d4f04934ff1dc73fe79ddf6401e442ff6941efa4080d6536588a2  attack-train.jsonl
f9b17c980b07de239018c69f50323e1d5d21132b2559cfd4520ca6ac1917c012  attack-validation.jsonl
1de6d22bb228b092e0f9b53b2e0f763e9569ee700eda15e822b05a2e9ca6e62a  pii-train.jsonl
5cfe0b6d6cfbb165f8bd722f0221cd86ace68ab358d2936a1fc8e71e253d6b2c  pii-validation.jsonl
```

⚠️ 이 값이 안 맞으면 **학습을 시작하지 않는다.** 원격이 다른 데이터를 보고 있다는 뜻이고,
그때 나오는 지표는 로컬에서 잰 어떤 것과도 비교할 수 없다.

## 환경

- **Python 3.11** — `requires-python = ">=3.11,<3.12"` 이다. 3.12 이미지를 고르면 `uv sync`
  가 거부한다.
- `uv.lock` 이 `manylinux_2_28_x86_64` 휠을 담고 있어 그대로 해석된다. PyPI 의 리눅스
  torch 휠은 CUDA 를 포함하므로 별도 인덱스가 필요 없다.
- `HF_TOKEN` 이 필요하다(gated 아닌 저장소도 rate limit 때문에 있는 편이 낫다).

```
git clone <repo> && cd safeagent-kr
uv sync --extra train
export HF_TOKEN=...
```

## 순서

```
1  python train/download_assets.py --execute
2  python train/prepare_corpora.py --ack-sensitive-synthetic-identifiers
                                   --polyguard-limit 20000
3  python train/merge_corpora.py  (pii train/validation, attack train/validation)
4  ★ 해시 대조 — 아래 값과 다르면 여기서 멈춘다
5  python train/make_teacher_input.py   × train·validation
5b python train/merge_corpora.py --task attack --split train
       --input data/corpus/attack-unlabeled-train.jsonl
       --input data/corpus/polyguard_train-train.jsonl
       --blocked-eval data/corpus/polyguard_eval-test.jsonl
       --output data/corpus/attack-teacher-pool-train.jsonl
6  python train/run_teacher.py --execute --device cuda   × 3 (kanana·gemma4·qwen3guard_gen)
       입력은 **동일한 pool 파일** 하나여야 한다 — ID 집합이 갈리면 합의가 성립하지 않는다
6b ★ python -m bench.teacher_agreement   → 동의율 ≥0.95 면 여기서 멈춘다
7  python train/resolve_teacher_labels.py --drop-review
8  python train/finalize_training_inputs.py     ← 학습 입력 봉인
8b python train/finalize_evaluation_inputs.py   ← 평가 입력 봉인
9  python train/preflight.py --stage training  → ok: true
10 python train/train_l1.py --execute --device cuda   (시드 3개)
11 python train/export_l1.py  →  python train/benchmark_l1.py
```

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
