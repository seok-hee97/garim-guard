#!/usr/bin/env python3
"""RunPod 2b~10단계를 한 명령으로 — 그리고 같은 경로를 로컬에서 먼저 리허설한다.

RUNPOD.md 의 단계는 긴 명령과 차단 목록이 README·RUNPOD·코드 세 곳에 따로 적혀 있었다.
리허설로 돌려 보니 교사 실행 **뒤** 단계에서 결함이 다섯 나왔다 — 6b 일치도 하네스의
gold 경로·범위, 7단계 template 분할, PolyGuard group 이 하위 데이터셋 둘로 뭉친 것,
8단계 교차 태스크 중복, 8b 평가 지문 중복. 전부 GPU 교사를 다 돌린 다음에야 드러날
자리였다. 이 스크립트가 단계의 유일한 출처다.

    # 로컬 리허설 — 교사 대역, 봉인·합의 산출물은 train/artifacts/rehearsal/, 학습은 dry-run
    python train/runpod_pipeline.py --rehearse

    # RunPod — 교사 3종(CUDA) → 게이트 → 합의 → 봉인 → preflight → 학습 dry-run
    python train/runpod_pipeline.py --device cuda
    # 학습까지
    python train/runpod_pipeline.py --device cuda --train

**리허설은 `data/` 의 봉인 파일을 쓰지 않는다.** 교사 대역은 실제 모델 출력이 아니며
(`prompt_version: rehearsal-standin`) 학습에 쓰면 안 된다 — 그래서 교사·합의·봉인 산출물은
Git 밖 `train/artifacts/rehearsal/` 에만 생기고, 학습은 `--execute` 없이 dry-run 으로만 돈다.
결정적인 코퍼스 단계(3·5a~5c — `data/merged/`, 교사 풀)는 RunPod 과 똑같이 다시 만든다.
그러면 그 이전 입력으로 만든 낡은 봉인은 검증에 실패한다 — 정상이다, 다시 봉인할 대상이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = Path("data/corpus")
TEACHERS = ("kanana", "gemma4", "qwen3guard_gen")

PII_TRAIN = ("data/merged/pii-train.jsonl",)
PII_VALIDATION = ("data/merged/pii-validation.jsonl",)

# 최종 평가 봉인 대상. 학습 데이터는 이 전부와 정규화 지문이 겹치면 안 되므로, 공격 교사
# 풀을 만들 때 같은 목록으로 거른다 — 두 곳에 따로 적으면 언젠가 갈린다.
EVALUATION_INPUTS = (
    CORPUS / "pii-synthetic-test.jsonl",
    CORPUS / "attack-synthetic-test.jsonl",
    CORPUS / "t4-test.jsonl",
    CORPUS / "t4-prime-test.jsonl",
    CORPUS / "attack-external-gold-test.jsonl",
    CORPUS / "kdpii-test.jsonl",
    CORPUS / "klue_eval-test.jsonl",
    CORPUS / "bc_pii_augmented_negative-test.jsonl",
)
# 교사 입력 길이 상한(kanana 토크나이저 기준). 근거 셋 — kanana 의 문맥이 8,192 라 그보다 긴
# 행은 읽지도 못한다(4행). 학생 L1 은 512토큰 창으로 보므로 2천 토큰이 넘는 문서 행의 문서 단위
# 라벨을 모든 창에 복사하면 라벨이 시끄러워진다. 이 위로는 38행(0.2%)이고 전부 PolyGuardMix 의
# 강의 녹취·논문류다. 빼고 감사 파일에 남긴다.
TEACHER_MAX_TOKENS = 2048
# 평가 봉인에는 안 들어가지만 공격 풀과 겹치면 안 되는 원천 test.
EXTRA_BLOCKED = (CORPUS / "polyguard_eval-test.jsonl", CORPUS / "deepset-ko-test.jsonl")


def _run(step: str, command: list[str]) -> int:
    print(f"\n== {step}\n   {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def _require(step: str, command: list[str], *, allowed: tuple[int, ...] = (0,)) -> int:
    code = _run(step, command)
    if code not in allowed:
        raise SystemExit(f"[{step}] 실패 (exit {code}) — 여기서 멈춘다")
    return code


def _python(*args: str) -> list[str]:
    return [sys.executable, *args]


def _digest(path: Path | str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def check_cuda() -> None:
    """0 — GPU 가 실제로 보이는지 먼저 본다. 교사 단계 안쪽에서 죽으면 원인이 멀어 보인다.

    `uv.lock` 의 torch 2.13 은 CUDA 13 런타임(`nvidia-*-cu13`)을 쓴다. pod 의 드라이버가
    CUDA 13 을 지원하지 않으면(`nvidia-smi` 의 CUDA Version < 13.0) torch 가 GPU 를 못 본다.
    """
    _require(
        "0 CUDA 확인",
        _python(
            "-c",
            "import torch, sys; ok = torch.cuda.is_available(); "
            "print('torch', torch.__version__, 'cuda', torch.version.cuda, 'available', ok, "
            "torch.cuda.get_device_name(0) if ok else ''); "
            "sys.exit(0 if ok else 'CUDA 를 못 본다 — 드라이버가 CUDA 13 을 지원하는 pod 인가')",
        ),
    )


def probe_gpu_memory() -> None:
    """0b — 학습 최악 배치를 교사보다 먼저 GPU 에서 돌려 본다(`train/probe_gpu_memory.py`)."""
    _require("0b GPU 메모리 프로브 (학습 최악 배치)", _python("train/probe_gpu_memory.py"))


def materialize_t4() -> None:
    """2b — T4·T4′ 를 생성기 test 분할에서 결정적으로 만든다.

    ★ 이 단계가 없었다. ba8f6d9 가 T4/T4′ 를 `data/corpus/` 에 한 번 손으로 써서 봉인했고,
    그것을 쓰는 커밋된 스크립트가 없었다 — 새 pod 에서는 3단계 차단 목록이 파일을 못 찾아
    멈춘다. 같은 함수로 다시 만들면 로컬본과 바이트 단위로 같다(2026-10-26 확인).
    """
    from garim.training.data import load_jsonl, write_jsonl
    from garim.training.t4 import generate_t4, generate_t4_prime

    holdout = load_jsonl(ROOT / CORPUS / "attack-synthetic-test.jsonl")
    for name, records in (
        ("t4-test.jsonl", generate_t4(holdout)),
        ("t4-prime-test.jsonl", generate_t4_prime(holdout)),
    ):
        write_jsonl(ROOT / CORPUS / name, records)
        print(f"\n== 2b {name} {len(records)}행  sha256 {_digest(CORPUS / name)}")


def merge_pii() -> None:
    """3 — PII 병합. 최종 평가셋과 겹치는 문장을 **학습 쪽에서** 뺀다.

    ★ 이 단계에 평가 차단 목록이 없었다. KDPII test 의 `네, 맞습니다.` 류 128행이 PII
      학습과 겹쳤고, 상류 KLUE 에는 라벨까지 같은 문장이 train·validation 양쪽에 있다
      (`김연아는 이날 오후 …`). 리허설 8b(평가 봉인)가 거기서 멈췄다. 평가셋은 §7.1.0 의
      유일한 외부 PII 재현율 증거라 건드리지 않고, 봉인 규칙도 느슨하게 하지 않는다.
    """
    blocked = [arg for path in EVALUATION_INPUTS for arg in ("--blocked-eval", str(path))]
    for split, inputs in (
        ("train", ("pii-synthetic-train", "kdpii-train", "klue_train-train")),
        ("validation", ("pii-synthetic-validation", "kdpii-validation")),
    ):
        output = PII_TRAIN[0] if split == "train" else PII_VALIDATION[0]
        sources = [arg for name in inputs for arg in ("--input", str(CORPUS / f"{name}.jsonl"))]
        _require(
            f"3 PII 병합 ({split})",
            _python("train/merge_corpora.py", "--task", "pii", "--split", split,
                    *sources, *blocked, "--output", output),
        )
        print(f"   sha256 {_digest(output)}  {output}")


def build_pool() -> Path:
    """5~5c — 생성기 라벨 분리, 번역 적용, 교사 풀 병합, train·validation 을 한 입력으로."""
    for split in ("train", "validation"):
        # 생성기 라벨은 교사에게 주지 않고 사이드카로 보관한다 — 6b 가 일치도를 잰다.
        _require(
            f"5 생성기 라벨 분리 ({split})",
            _python(
                "train/make_teacher_input.py",
                "--input", str(CORPUS / f"attack-synthetic-{split}.jsonl"),
                "--output", str(CORPUS / f"attack-unlabeled-{split}.jsonl"),
                "--gold", str(CORPUS / f"attack-generator-gold-{split}.json"),
            ),
        )
    deepset = (("train", "deepset_train_queue-train"), ("test", "deepset_eval_queue-test"))
    for split, queue in deepset:
        _require(
            f"5a deepset 번역 적용 ({split})",
            _python(
                "train/apply_translations.py",
                "--queue", str(CORPUS / f"{queue}.jsonl"),
                "--translations", f"train/translations/deepset-{split}-ko.jsonl",
                "--output", str(CORPUS / f"deepset-ko-{split}.jsonl"),
            ),
        )
    _require(
        "5a deepset 외부 이진 gold",
        _python(
            "train/apply_external_eval_labels.py",
            "--input", str(CORPUS / "deepset-ko-test.jsonl"),
            "--output", str(CORPUS / "attack-external-gold-test.jsonl"),
        ),
    )
    evaluation = (*EVALUATION_INPUTS, *EXTRA_BLOCKED)
    blocked = [
        *(arg for path in evaluation for arg in ("--blocked-eval", str(path))),
        *(arg for path in (*PII_TRAIN, *PII_VALIDATION) for arg in ("--blocked-other-task", path)),
    ]
    pools = {
        # 순서가 우선순위다 — 중복이면 앞의 것이 남는다.
        "train": ("attack-unlabeled-train", "polyguard_train-train", "deepset-ko-train"),
        # 검증 풀은 합성만이다. 공개 원천은 교사 합의 뒤 7단계가 성분 단위로 검증을 떼어 낸다.
        "validation": ("attack-unlabeled-validation",),
    }
    for split, inputs in pools.items():
        sources = [arg for name in inputs for arg in ("--input", str(CORPUS / f"{name}.jsonl"))]
        _require(
            f"5b 공격 교사 풀 ({split})",
            _python("train/merge_corpora.py", "--task", "attack", "--split", split, *sources,
                    *blocked, "--output", str(CORPUS / f"attack-teacher-pool-{split}.jsonl")),
        )
    from garim.training.compat import load_local_tokenizer
    from garim.training.data import load_jsonl, write_jsonl

    tokenizer = load_local_tokenizer(ROOT / "models/pinned/teacher_kanana")
    kept, overlong = [], []
    for part in ("attack-teacher-pool-train.jsonl", "attack-teacher-pool-validation.jsonl"):
        for record in load_jsonl(ROOT / CORPUS / part):
            tokens = len(tokenizer(record.text)["input_ids"])
            (kept if tokens <= TEACHER_MAX_TOKENS else overlong).append(record)
    teacher_input = CORPUS / "attack-teacher-input.jsonl"
    write_jsonl(ROOT / teacher_input, kept)
    write_jsonl(ROOT / CORPUS / "attack-teacher-overlength.jsonl", overlong)
    print(
        f"\n== 5c 교사 입력 {len(kept)}행 (>{TEACHER_MAX_TOKENS}토큰 {len(overlong)}행 제외 → "
        f"attack-teacher-overlength.jsonl)  sha256 {_digest(teacher_input)}"
    )
    return teacher_input


def _bucket(record_id: str, salt: str) -> float:
    return int(hashlib.sha256(f"{salt}:{record_id}".encode()).hexdigest()[:8], 16) / 2**32


def write_standin_teachers(teacher_input: Path, out_dir: Path) -> dict[str, Path]:
    """리허설 전용 교사 대역. 형식·ID 범위·투표 구조만 실제와 같고 라벨은 지어낸 것이다."""
    from garim.training.data import load_jsonl
    from garim.training.teachers import load_teacher_policy

    policy = load_teacher_policy("train/teachers.yaml")
    gold: dict[str, str] = {}
    for split in ("train", "validation"):
        gold.update(json.loads((ROOT / CORPUS / f"attack-generator-gold-{split}.json").read_text()))
    paths = {name: out_dir / f"teacher-{name}.jsonl" for name in TEACHERS}
    streams = {name: (ROOT / path).open("w", encoding="utf-8") for name, path in paths.items()}
    try:
        for record in load_jsonl(ROOT / teacher_input):
            if record.id in gold:
                label = gold[record.id]
            elif record.meta.get("original_binary_label") is not None:
                label = "INJECTION" if record.meta["original_binary_label"] == 1 else "NONE"
            else:
                u = _bucket(record.id, "label")
                label = ("NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK")[
                    0 if u < 0.7 else 1 if u < 0.8 else 2 if u < 0.9 else 3
                ]
            kanana = label if label != "JAILBREAK" else "INJECTION"
            if _bucket(record.id, "disagree") < 0.15:  # 일부는 합의 실패로 폐기 경로를 탄다
                kanana = "NONE" if kanana != "NONE" else "INJECTION"
            probabilities = {"NONE": 0.05, "INJECTION": 0.05, "SYSTEM_LEAK": 0.05}
            probabilities[kanana] = 0.9
            jailbreak = 0.9 if label == "JAILBREAK" and _bucket(record.id, "qwen") < 0.8 else 0.05
            rows = {
                "kanana": {"label": kanana, "confidence": 0.9,
                           "class_probabilities": probabilities},
                "gemma4": {"label": label, "confidence": 1.0},
                "qwen3guard_gen": {"label": "JAILBREAK" if jailbreak > 0.5 else "NONE",
                                   "confidence": max(jailbreak, 1 - jailbreak),
                                   "class_probabilities": {"JAILBREAK": jailbreak}},
            }
            for name, row in rows.items():
                spec = policy.teachers[name]
                streams[name].write(json.dumps({
                    "id": record.id, "teacher": name, "model_id": spec.model_id,
                    "revision": spec.revision, "prompt_version": "rehearsal-standin", **row,
                }) + "\n")
    finally:
        for stream in streams.values():
            stream.close()
    return paths


def run_teachers(teacher_input: Path, device: str, batch_size: int) -> dict[str, Path]:
    """6 — 교사 3종. `run_teacher.py` 가 이어 돌기를 지원하므로 끊겨도 다시 부르면 된다."""
    paths = {name: CORPUS / f"teacher-{name}.jsonl" for name in TEACHERS}
    for name, path in paths.items():
        _require(
            f"6 교사 {name}",
            _python(
                "train/run_teacher.py", "--teacher", name,
                "--model-dir", f"models/pinned/teacher_{name}",
                "--input", str(teacher_input), "--output", str(path),
                "--device", device, "--batch-size", str(batch_size), "--execute",
            ),
        )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--rehearse", action="store_true", help="교사 대역 + Git 밖 산출물 + dry-run"
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--train", action="store_true", help="dry-run 뒤 3-seed 학습까지 실행")
    parser.add_argument(
        "--accept-rubber-stamp",
        action="store_true",
        help="6b JAILBREAK 동의율 게이트를 넘었어도 계속한다(사람이 판단한 뒤에만)",
    )
    args = parser.parse_args()
    if args.rehearse and args.train:
        parser.error("리허설 산출물(교사 대역)로는 학습하지 않는다")

    work = Path("train/artifacts/rehearsal") if args.rehearse else Path("data")
    resolved = work / "resolved" if args.rehearse else CORPUS / "resolved"
    training_manifest = work / "training-input-manifest.json"
    evaluation_manifest = work / "evaluation-input-manifest.json"
    (ROOT / work).mkdir(parents=True, exist_ok=True)

    if not args.rehearse:
        check_cuda()
        probe_gpu_memory()
    materialize_t4()
    merge_pii()
    teacher_input = build_pool()
    if args.rehearse:
        teachers = write_standin_teachers(teacher_input, work)
        print(f"\n== 6 교사 대역 {len(teachers)}종 -> {work} (리허설 전용)")
    else:
        teachers = run_teachers(teacher_input, args.device, args.batch_size)

    teacher_args = [
        arg for name, path in teachers.items() for arg in ("--teacher", f"{name}={path}")
    ]
    gate = _require(
        "6b 교사 일치도 · JAILBREAK 독립성",
        _python("-m", "bench.teacher_agreement", *teacher_args,
                "--output", str(work / "teacher_agreement.json")),
        allowed=(0, 2),
    )
    if gate == 2 and not (args.rehearse or args.accept_rubber_stamp):
        raise SystemExit(
            "[6b] JAILBREAK 동의율이 임계를 넘었다 — 두 번째 교사가 검증 효과를 못 낸다. "
            "RUNPOD.md 의 대안을 검토하고, 그래도 진행하려면 --accept-rubber-stamp"
        )
    _require(
        "7 교사 합의 (사람 검수 없음 — 불일치는 감사 기록과 함께 폐기)",
        _python("train/resolve_teacher_labels.py", "--input", str(teacher_input),
                *teacher_args, "--output-dir", str(resolved), "--drop-review"),
    )
    corpora: list[str] = []
    for flag, paths in (
        ("--pii-train", PII_TRAIN),
        ("--pii-validation", PII_VALIDATION),
        ("--attack-train", (str(resolved / "attack-teacher-accepted-train.jsonl"),)),
        ("--attack-validation", (str(resolved / "attack-teacher-accepted-validation.jsonl"),)),
    ):
        for path in paths:
            corpora += [flag, path]
    _require(
        "8 학습 입력 봉인",
        _python("train/finalize_training_inputs.py", *corpora, "--output", str(training_manifest)),
    )
    evaluation = [arg for path in EVALUATION_INPUTS for arg in ("--input", str(path))]
    _require(
        "8b 평가 입력 봉인",
        _python("train/finalize_evaluation_inputs.py", *evaluation,
                "--training-input-manifest", str(training_manifest),
                "--output", str(evaluation_manifest)),
    )
    manifests = ["--input-manifest", str(training_manifest),
                 "--evaluation-manifest", str(evaluation_manifest)]
    encoder = ["--encoder-dir", "models/pinned/l1_ax_encoder"]
    _require(
        "9 preflight --stage training",
        _python("train/preflight.py", "--stage", "training", *encoder, *corpora, *manifests),
    )
    _require(
        "10 학습 dry-run (tokenizer·스팬 정렬, 4-head 단일 forward, 누수·미해결 게이트)",
        _python("train/train_l1.py", *encoder, *corpora, *manifests),
    )
    if args.train:
        _require(
            "10 학습 (3 seed)",
            _python("train/train_l1.py", *encoder, *corpora, *manifests,
                    "--device", args.device, "--execute"),
        )
    print("\n리허설 통과 — 교사 대역으로 3~10 배선을 끝까지 돌렸다." if args.rehearse
          else "\n완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
