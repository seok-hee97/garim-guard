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
# 판정은 `garim.detect.rules.detect_kr_pii` 로 data/ 전체를 훑어 정했다(RRN · FRN 이 나온 파일만).
PRIVATE_PREFIXES = ("raw/", "merged/", "runpod/2026-10-27/merged/")
PRIVATE_FILES = frozenset(
    f"corpus/{stem}-{split}.jsonl"
    for stem in ("kdpii", "pii-synthetic")
    for split in ("train", "validation", "test")
)
# model 저장소에 이미 있는 판정용 ONNX 사본.
SKIP_PREFIXES = ("runpod/2026-10-27-r2/final/train/artifacts/l1-r2-onnx-rr/",)
SKIP_PARTS = {".cache", ".git", "__pycache__", "kotip"}

# (이름, 저장소, 종류, 스테이징, 패턴) — 앞일수록 다시 만들기 어렵다.
PHASES = (
    ("배포 R1 INT8", MODEL, "model", "model",
     ["l1/r1/onnx/garim_l1.int8.onnx", "l1/r1/onnx/*.json"]),
    ("공개 데이터 · 학습 기록", DATASET, "dataset", "public", None),
    ("R2 INT8 · 학습 보고서", MODEL, "model", "model", [
        "l1/r2/onnx/garim_l1.int8.onnx", "l1/r2/onnx/*.json", "l1/*/checkpoints/*.json",
        "l1/*/checkpoints/*/training-report.json", "l1/*/checkpoints/*/encoder/*",
    ]),
    ("배포 seed 37 체크포인트", MODEL, "model", "model", ["l1/*/checkpoints/seed-37/joint_l1.pt"]),
    ("FP32 · INT8 원본", MODEL, "model", "model", ["l1/*/onnx/*"]),
    ("비공개 — 합성 주민번호 파일", PRIVATE, "dataset", "private",
     ["corpus/**", "merged/**", "runpod/**"]),
    ("나머지 seed 체크포인트", MODEL, "model", "model", ["l1/**"]),
    ("비공개 — 원천 데이터", PRIVATE, "dataset", "private", ["raw/**"]),
    ("L2 GGUF", MODEL, "model", "model", ["l2/**"]),
)


def _files(source: Path):
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name != ".DS_Store" and not SKIP_PARTS & set(path.parts):
            yield path


def data_bucket(relative: str) -> str:
    if relative.startswith(SKIP_PREFIXES):
        return "skip"
    if relative.startswith(PRIVATE_PREFIXES) or relative in PRIVATE_FILES:
        return "private"
    return "public"


def plan() -> dict[str, list[tuple[Path, str]]]:
    """스테이징 이름 → (원본, 저장소 안 경로) 목록."""
    staged: dict[str, list[tuple[Path, str]]] = {"model": [], "public": [], "private": []}
    for source, target in MODEL_TREE.items():
        for path in _files(ROOT / source):
            staged["model"].append((path, f"{target}/{path.relative_to(ROOT / source).as_posix()}"))
    for path in _files(ROOT / "data"):
        relative = path.relative_to(ROOT / "data").as_posix()
        bucket = data_bucket(relative)
        if bucket != "skip":
            staged[bucket].append((path, relative))
    for path in sorted((ROOT / "train/artifacts").glob("*.json")):
        staged["public"].append((path, f"results/r1/{path.name}"))
    return staged


def stage(staged: dict[str, list[tuple[Path, str]]], workdir: Path) -> None:
    for name, entries in staged.items():
        for source, relative in entries:
            target = workdir / name / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:  # 다른 볼륨이면 복사한다
                shutil.copy2(source, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workdir", default=str(ROOT / ".hf-staging"),
                        help="하드링크 스테이징 위치(같은 볼륨이어야 디스크를 안 쓴다). Git 밖이다")
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
    workdir = Path(args.workdir)
    stage(staged, workdir)
    for name, repo, kind, folder, patterns in PHASES:
        print(f"== {name} → {kind} {repo}", flush=True)
        api.upload_large_folder(
            repo_id=repo, repo_type=kind, folder_path=str(workdir / folder),
            allow_patterns=patterns, num_workers=4, print_report=False,
        )
    print("완료", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
