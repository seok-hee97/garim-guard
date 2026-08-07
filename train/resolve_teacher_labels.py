#!/usr/bin/env python3
"""Resolve three pinned teacher artifacts into accepted and human-review queues."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from garim.training.data import load_jsonl, validate_corpus, write_jsonl
from garim.training.teacher_io import load_teacher_artifacts, resolve_teacher_corpus
from garim.training.teachers import load_teacher_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="unlabeled Korean attack JSONL")
    parser.add_argument("--kanana", required=True)
    parser.add_argument("--gemma4", required=True)
    parser.add_argument("--qwen3guard-stream", required=True)
    parser.add_argument("--policy", default="train/teachers.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--validation-percent", type=int, default=10)
    args = parser.parse_args()

    records = load_jsonl(args.input)
    policy = load_teacher_policy(args.policy)
    artifacts = {
        "kanana": load_teacher_artifacts(args.kanana),
        "gemma4": load_teacher_artifacts(args.gemma4),
        "qwen3guard_stream": load_teacher_artifacts(args.qwen3guard_stream),
    }
    accepted, review, audit = resolve_teacher_corpus(
        records,
        artifacts,
        policy,
        validation_percent=args.validation_percent,
    )
    validate_corpus(accepted)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation"):
        write_jsonl(
            output / f"attack-teacher-accepted-{split}.jsonl",
            [record for record in accepted if record.split == split],
        )
    write_jsonl(output / "attack-teacher-review.jsonl", review)
    (output / "teacher-resolution-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"accepted={len(accepted)} review={len(review)} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
