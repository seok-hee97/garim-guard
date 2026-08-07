#!/usr/bin/env python3
"""Apply blinded human adjudication to teacher-review rows with exact ID coverage."""

from __future__ import annotations

import argparse
from pathlib import Path

from garim.training.data import load_jsonl, write_jsonl
from garim.training.manual_labels import apply_review_labels, load_manual_labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--validation-percent", type=int, default=10)
    args = parser.parse_args()
    if not 0 <= args.validation_percent < 100:
        raise ValueError("validation-percent must be in [0, 100)")

    reviewed = apply_review_labels(
        load_jsonl(args.queue),
        load_manual_labels(args.labels),
        validation_percent=args.validation_percent,
    )
    output = Path(args.output_dir)
    for split in ("train", "validation"):
        write_jsonl(
            output / f"attack-human-reviewed-{split}.jsonl",
            [record for record in reviewed if record.split == split],
        )
    print(f"applied {len(reviewed)} human review labels -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
