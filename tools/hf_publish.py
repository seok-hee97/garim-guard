#!/usr/bin/env python3
"""학습된 모델 · 데이터를 Hugging Face 세 저장소에 올린다 — 다시 만들기 어려운 것부터.

- model `cycloevan/garim-guard` — `l1/r1` · `l1/r2`(ONNX · 체크포인트), `l2/gguf`
- dataset `cycloevan/garim-guard` — `data/` 중 비공개 규칙에 안 걸리는 것 + `results/r1/`
- dataset `cycloevan/garim-guard-private` — 체크섬을 통과하는 합성 주민등록번호가 든 파일,
  원천 원본(`raw/`)

★ 업로드는 Xet 대신 LFS 로 한다(`HF_HUB_DISABLE_XET=1`). 같은 회선에서 20MB 가 Xet 은 10분을
  넘겼고 LFS 는 24초였다(844KB/s). 스테이징은 하드링크라 디스크를 더 쓰지 않고,
  `upload_large_folder` 가 끊긴 곳부터 다시 올린다.

    python tools/hf_publish.py              # 무엇을 어디로 올릴지만 출력
    python tools/hf_publish.py --execute    # 올린다(같은 명령을 다시 부르면 이어 올린다)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python tools/hf_publish.py` 로 부를 때 garim · tools 를 찾게
    sys.path.insert(0, str(ROOT))

from garim.detect.rules import detect_secrets  # noqa: E402
from tools.no_committed_pii import resident_candidates  # noqa: E402

MODEL = DATASET = "cycloevan/garim-guard"
PRIVATE = "cycloevan/garim-guard-private"

# 프로젝트 경로 → 저장소 안 경로. 모델은 배포 이름(l1-onnx)이 아니라 학습 차수(r1 · r2)로 묶는다.
MODEL_TREE = {
    "train/artifacts/l1-onnx": "l1/r1/onnx",
    "train/artifacts/l1": "l1/r1/checkpoints",
    "train/artifacts/l1-r2-onnx": "l1/r2/onnx",
    "train/artifacts/l1-r2": "l1/r2/checkpoints",
    "models/derived": "l2/gguf",
}
# `data/` 중 비공개 — 체크섬을 통과하는 합성 · 비식별 원천 주민등록번호가 든 파일과 원천 원본.
#
# ★ 처음엔 이름 규칙만 있었고, 규칙에 없는 것은 전부 공개로 갔다. 쓰다 만
#   `pii-synthetic-train.jsonl.tmp` · `pii-synthetic-r3-train.jsonl` · 새 날짜의
#   `runpod/<날짜>/merged/` 가 모두 공개로 분류됐다(전수 검토 2026-10-28). 이제 이름 규칙은
#   비공개로 보내는 쪽으로만 쓰고, 공개 후보는 내용을 읽어 확인한다 — 읽을 수 없거나 체크섬 유효
#   주민번호 후보 · 키가 있으면 비공개로 보낸다.
PRIVATE_PREFIXES = ("raw/", "merged/", "runpod/2026-10-27/merged/")
PRIVATE_PARTS = frozenset({"raw", "merged"})  # 경로 어느 깊이에 있어도
PRIVATE_NAME_MARKERS = ("pii-synthetic", "kdpii")
PRIVATE_FILES = frozenset(
    f"corpus/{stem}-{split}.jsonl"
    for stem in ("kdpii", "pii-synthetic")
    for split in ("train", "validation", "test")
)
TEMP_SUFFIXES = (".tmp", ".part", ".partial", ".swp", "~")
# 공개 데이터에 있으면 안 되는 키 — 형식이 확실한 것만(자리표시 · 문맥 엔트로피는 뺀다).
_PUBLISH_BLOCKING_SECRETS = frozenset({"private_key_header", "database_url"})
# model 저장소에 이미 있는 판정용 ONNX 사본.
SKIP_PREFIXES = ("runpod/2026-10-27-r2/final/train/artifacts/l1-r2-onnx-rr/",)
SKIP_PARTS = {".cache", ".git", "__pycache__", "kotip"}

# (이름, 저장소, 종류, 스테이징, 패턴) — 앞일수록 다시 만들기 어렵다.
PHASES = (
    (
        "배포 R1 INT8",
        MODEL,
        "model",
        "model",
        ["l1/r1/onnx/garim_l1.int8.onnx", "l1/r1/onnx/*.json"],
    ),
    ("공개 데이터 · 학습 기록", DATASET, "dataset", "public", None),
    (
        "R2 INT8 · 학습 보고서",
        MODEL,
        "model",
        "model",
        [
            "l1/r2/onnx/garim_l1.int8.onnx",
            "l1/r2/onnx/*.json",
            "l1/*/checkpoints/*.json",
            "l1/*/checkpoints/*/training-report.json",
            "l1/*/checkpoints/*/encoder/*",
        ],
    ),
    ("배포 seed 37 체크포인트", MODEL, "model", "model", ["l1/*/checkpoints/seed-37/joint_l1.pt"]),
    ("FP32 · INT8 원본", MODEL, "model", "model", ["l1/*/onnx/*"]),
    (
        "비공개 — 합성 주민번호 파일",
        PRIVATE,
        "dataset",
        "private",
        ["corpus/**", "merged/**", "runpod/**"],
    ),
    ("나머지 seed 체크포인트", MODEL, "model", "model", ["l1/**"]),
    ("비공개 — 원천 데이터", PRIVATE, "dataset", "private", ["raw/**"]),
    ("L2 GGUF", MODEL, "model", "model", ["l2/**"]),
)


def _files(source: Path):
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name != ".DS_Store" and not SKIP_PARTS & set(path.parts):
            yield path


def data_bucket(relative: str) -> str:
    """이름만으로 정한다 — 공개 후보는 `plan()` 이 내용을 한 번 더 본다."""
    name = relative.rsplit("/", 1)[-1]
    if relative.startswith(SKIP_PREFIXES) or name.endswith(TEMP_SUFFIXES):
        return "skip"  # 쓰다 만 파일은 어디에도 올리지 않는다
    if (
        relative.startswith(PRIVATE_PREFIXES)
        or relative in PRIVATE_FILES
        or PRIVATE_PARTS & set(relative.split("/")[:-1])
        or any(marker in name for marker in PRIVATE_NAME_MARKERS)
    ):
        return "private"
    return "public"


def publish_blocker(path: Path) -> str | None:
    """공개하면 안 되는 이유. 값은 돌려주지 않는다."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "not UTF-8 text"
    if resident_candidates(text):
        return "checksum-valid resident-number candidate"
    for finding in detect_secrets(text):
        if finding.rule in _PUBLISH_BLOCKING_SECRETS or (
            finding.code == "L-SEC-01" and finding.confidence >= 1.0
        ):
            return f"secret ({finding.rule})"
    return None


def plan() -> dict[str, list[tuple[Path, str]]]:
    """스테이징 이름 → (원본, 저장소 안 경로) 목록."""
    staged: dict[str, list[tuple[Path, str]]] = {"model": [], "public": [], "private": []}
    for source, target in MODEL_TREE.items():
        for path in _files(ROOT / source):
            staged["model"].append((path, f"{target}/{path.relative_to(ROOT / source).as_posix()}"))
    for path in _files(ROOT / "data"):
        relative = path.relative_to(ROOT / "data").as_posix()
        bucket = data_bucket(relative)
        if bucket == "public" and (reason := publish_blocker(path)) is not None:
            print(f"비공개로 보냄: {relative} — {reason}", file=sys.stderr)
            bucket = "private"
        if bucket != "skip":
            staged[bucket].append((path, relative))
    for path in sorted((ROOT / "train/artifacts").glob("*.json")):
        if (reason := publish_blocker(path)) is not None:
            # results/ 는 비공개 저장소에 자리가 없다 — 올리지 않고 알린다.
            print(f"올리지 않음: {path.relative_to(ROOT)} — {reason}", file=sys.stderr)
            continue
        staged["public"].append((path, f"results/r1/{path.name}"))
    return staged


def stage(staged: dict[str, list[tuple[Path, str]]], workdir: Path) -> None:
    """스테이징을 이번 계획과 똑같이 만든다.

    ★ 처음엔 있는 파일을 건너뛰기만 했다. 지난 실행에 공개로 스테이징됐다가 비공개 규칙으로 옮긴
      파일이 `public/` 에 남아 다시 공개로 올라갔고, 임시 파일 → rename 으로 고친 원본은 옛 내용이
      올라갔다. 매번 비우고 다시 건다(하드링크라 싸다). `.cache/` 는 이어 올리기 기록이라 둔다.
    """
    for name, entries in staged.items():
        folder = workdir / name
        if folder.is_dir():
            for child in folder.iterdir():
                if child.name == ".cache":
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        for source, relative in entries:
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:  # 다른 볼륨이면 복사한다
                shutil.copy2(source, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--workdir",
        default=str(ROOT / ".hf-staging"),
        help="하드링크 스테이징 위치(같은 볼륨이어야 디스크를 안 쓴다). Git 밖이다",
    )
    args = parser.parse_args()

    staged = plan()
    for name, entries in staged.items():
        size = sum(source.stat().st_size for source, _ in entries)
        print(f"{name:8} {len(entries):4d}개 {size / 1e9:6.2f}GB")
    if not args.execute:
        return 0

    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import HfApi

    api = HfApi()
    for repo, kind in ((MODEL, "model"), (DATASET, "dataset"), (PRIVATE, "dataset")):
        api.create_repo(repo, repo_type=kind, private=True, exist_ok=True)
    # ★ `exist_ok` 는 이미 있는 저장소의 공개 여부를 보지 않는다. 비공개 저장소가 공개로 바뀌어
    #   있으면 합성 주민번호 파일이 그대로 공개된다 — 올리기 전에 멈춘다.
    if not api.repo_info(PRIVATE, repo_type="dataset").private:
        print(f"{PRIVATE} 가 비공개가 아니다 — 올리지 않는다", file=sys.stderr)
        return 1
    workdir = Path(args.workdir)
    stage(staged, workdir)
    for name, repo, kind, folder, patterns in PHASES:
        print(f"== {name} → {kind} {repo}", flush=True)
        api.upload_large_folder(
            repo_id=repo,
            repo_type=kind,
            folder_path=str(workdir / folder),
            allow_patterns=patterns,
            num_workers=4,
            print_report=False,
        )
    print("완료", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
