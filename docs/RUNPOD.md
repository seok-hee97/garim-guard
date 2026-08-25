# RunPod 실행 절차

로컬(M1 / 16GB)에서 못 하는 것만 여기서 한다 — **교사 라벨링**과 **R1/R2 학습**이다.
왜 못 하는지는 실측으로 확인했다.

| | 로컬 | 이유 |
|---|---|---|
| kanana 2.1b 추론 | ✅ | 4.19GB, MPS 로 돈다 |
| gemma-4-E2B 추론 | ❌ | 10.28GB 를 16GB 에서 돌리면 스왑 — 4행에 10분 초과 |
| Qwen3Guard-Stream | ❌ | 하드웨어 무관 — 아래 **열린 결정** 참조 |
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
3  python train/merge_corpora.py  (pii train/validation, attack train/validation)
4  ★ 해시 대조 — 위 네 값과 같은지 확인. 다르면 여기서 멈춘다
5  python train/make_teacher_input.py      → 라벨 벗긴 1,423행 + gold 사이드카
6  python train/run_teacher.py --execute   × 교사 3종
7  python train/resolve_teacher_labels.py --drop-review
8  python train/finalize_training_inputs.py
9  python train/preflight.py --stage training  → ok: true 여야 한다
10 python train/train_l1.py --execute
11 python train/export_l1.py
```

⚠️ **8번(봉인)이 5~7번 뒤에 온다.** 교사 라벨이 붙은 코퍼스를 봉인해야 한다. 봉인은
`train/training.yaml` 까지 덮으므로 하이퍼파라미터를 바꾸면 **재봉인**이다 — 바꾸고
안 하면 `training input path drift` 로 막힌다.

## 열린 결정 하나 — Qwen3Guard-Stream

`Qwen/Qwen3Guard-Stream-0.6B` 의 원격 코드가 transformers 4.x 를 벤더링한 것이라 우리 pin
(`>=5.5,<6`)에서 안 돈다. 상류가 안 고쳤다(우리 고정본 = 현재 main, 2025-11-07 이후 변경
없음). **하드웨어 문제가 아니라 RunPod 에서도 같다.**

그런데 **뺄 수가 없다.** `train/teachers.yaml` 을 보면:

```
minimum_votes: 2
kanana:  votes_for: [NONE, INJECTION, SYSTEM_LEAK]   ← JAILBREAK 없음
gemma4:  votes_for: [NONE, INJECTION, JAILBREAK, SYSTEM_LEAK]
qwen:    votes_for: [JAILBREAK]                       ← 전담
```

JAILBREAK 에 투표하는 교사가 둘뿐인데 정족수가 2 다. 빼면 JAILBREAK 가 영영 정족수를
못 채우고 `--drop-review` 에서 전부 폐기된다 — **4개 클래스 중 하나가 소멸한다.**

| | JAILBREAK | 비용 |
|---|---|---|
| 4.x 격리 venv | 유지 | 우리 pin 과 충돌 · 상류 미유지보수 |
| `Qwen3Guard-Gen-0.6B` 교체 | 유지 | 원격 코드 없음 · **다만 §6.2.2 가 이 모델의 폴란드 FPR 17.17% 를 명시**했다 |
| 제거 | **소멸** | — |

Gen 으로 바꾸면 §6.2.2 가 걸어 둔 방어 장치 셋(`votes_for: [JAILBREAK]` ·
`minimum_votes: 2` · `can_flip_none: false`)이 그대로 작동해 과탐 피해가 "Gemma 가 이미
JAILBREAK 이라 한 행에 동의" 로 제한된다. 다만 **과탐이 심하면 거의 항상 동의해서 사실상
Gemma 단독 판정이 된다** — 설계가 막으려던 상태다. 그래서 교체한다면 **JAILBREAK 동의율을
함께 재고**, 지나치게 높으면 교체 실패로 보고 venv 로 돌아간다.

## 학습 전 확인할 것 하나 — 태스크 불균형

```
PII 108,588 : 공격 1,238 = 88:1   (§6 추정은 54:1)
pii_per_attack=4 → 공격 1행을 에폭당 22번, 5에폭이면 110번
```

T4 는 평가 전용이라 이 비율을 바꾸지 않는다. 그대로 갈지, `pii_per_attack` 을 올릴지,
공격 학습 풀을 키울지는 **9번 preflight 통과 후 10번 전에** 정한다. 바꾸면 재봉인이다.
