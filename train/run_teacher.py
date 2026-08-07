#!/usr/bin/env python3
"""Run one pinned local teacher and write resumable JSONL prediction artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from garim.training.data import load_jsonl
from garim.training.teacher_inference import load_teacher_backend
from garim.training.teacher_io import load_teacher_artifacts
from garim.training.teachers import load_teacher_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--teacher",
        required=True,
        choices=("kanana", "gemma4", "qwen3guard_stream"),
    )
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--policy", default="train/teachers.yaml")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    policy = load_teacher_policy(args.policy)
    spec = policy.teachers[args.teacher]
    records = load_jsonl(args.input)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        records = records[: args.limit]
    if any(record.task != "attack" or record.attack_label is not None for record in records):
        raise ValueError("teacher input must contain only unlabeled attack records")
    if not args.execute:
        print(
            f"dry-run teacher={args.teacher} model={spec.model_id}@{spec.revision} "
            f"records={len(records)} local_dir={args.model_dir}"
        )
        return 0

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = load_teacher_artifacts(output) if output.is_file() else {}
    backend = load_teacher_backend(args.teacher, spec, args.model_dir, device=args.device)
    mode = "a" if output.is_file() else "w"
    with output.open(mode, encoding="utf-8") as stream:
        for index, record in enumerate(records, start=1):
            if record.id in completed:
                continue
            artifact = backend.predict(record)
            stream.write(artifact.model_dump_json(exclude_none=True) + "\n")
            stream.flush()
            if index % 100 == 0:
                print(f"{args.teacher}: {index}/{len(records)}")
    print(f"{args.teacher}: predictions complete -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
