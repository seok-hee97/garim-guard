#!/usr/bin/env python3
"""Export a metadata-free queue for blinded human attack labeling."""

from __future__ import annotations

import argparse

from garim.training.data import load_jsonl
from garim.training.manual_labels import blind_labeling_rows, write_blind_labeling_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument(
        "--purpose",
        choices=("teacher-review", "final-evaluation"),
        required=True,
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = [record for path in args.input for record in load_jsonl(path)]
    rows = blind_labeling_rows(records, purpose=args.purpose)
    count = write_blind_labeling_rows(args.output, rows)
    print(f"wrote {count} blinded {args.purpose} rows -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
