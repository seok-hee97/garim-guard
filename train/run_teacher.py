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
    # ★ 교사 목록을 손으로 적지 않는다. `teachers.yaml` 이 유일한 출처이고, 여기에
    #   또 적으면 교사를 바꿀 때 한쪽만 고치게 된다 — 실제로 Stream → Gen 교체에서
    #   정책은 바꿨는데 이 목록이 남아 `invalid choice` 로 막혔다.
    parser.add_argument("--teacher", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--policy", default="train/teachers.yaml")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="bounded inference batch (backends without batching preserve single-row semantics)",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    policy = load_teacher_policy(args.policy)
    if args.teacher not in policy.teachers:
        raise SystemExit(
            f"unknown teacher {args.teacher!r}; {args.policy} defines "
            f"{sorted(policy.teachers)}"
        )
    spec = policy.teachers[args.teacher]
    records = load_jsonl(args.input)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        records = records[: args.limit]
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
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
    record_ids = {record.id for record in records}
    extra = set(completed) - record_ids
    if extra:
        raise ValueError(f"existing output has {len(extra)} IDs outside the selected input")
    for artifact in completed.values():
        if artifact.teacher != args.teacher:
            raise ValueError(f"{artifact.id}: existing output teacher name mismatch")
        if artifact.model_id != spec.model_id or artifact.revision != spec.revision:
            raise ValueError(f"{artifact.id}: existing output model/revision drift")
    backend = load_teacher_backend(args.teacher, spec, args.model_dir, device=args.device)
    mode = "a" if output.is_file() else "w"
    pending = [record for record in records if record.id not in completed]
    written = len(completed)
    with output.open(mode, encoding="utf-8") as stream:
        for offset in range(0, len(pending), args.batch_size):
            batch = pending[offset : offset + args.batch_size]
            artifacts = backend.predict_many(batch)
            if [artifact.id for artifact in artifacts] != [record.id for record in batch]:
                raise ValueError("teacher backend returned incomplete or reordered batch IDs")
            for artifact in artifacts:
                stream.write(artifact.model_dump_json(exclude_none=True) + "\n")
            stream.flush()
            written += len(artifacts)
            if written % 100 < len(artifacts) or written == len(records):
                print(f"{args.teacher}: {written}/{len(records)}")
    print(f"{args.teacher}: predictions complete -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
