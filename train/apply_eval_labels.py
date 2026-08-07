#!/usr/bin/env python3
"""Apply exact-coverage human gold labels to sealed attack test rows."""

from __future__ import annotations

import argparse

from garim.training.data import load_jsonl, write_jsonl
from garim.training.manual_labels import apply_evaluation_labels, load_manual_labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = [record for path in args.input for record in load_jsonl(path)]
    labeled = apply_evaluation_labels(records, load_manual_labels(args.labels))
    count = write_jsonl(args.output, labeled)
    print(f"applied {count} blinded final-evaluation labels -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
