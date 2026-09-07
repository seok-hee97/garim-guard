#!/usr/bin/env python3
"""Apply only taxonomy-preserving external labels to final attack evaluation rows."""

from __future__ import annotations

import argparse

from garim.training.data import load_jsonl, validate_corpus, write_jsonl
from garim.training.external_labels import apply_external_eval_labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = [record for path in args.input for record in load_jsonl(path)]
    labeled = list(apply_external_eval_labels(records))
    validate_corpus(labeled)
    count = write_jsonl(args.output, labeled)
    print(f"applied {count} taxonomy-preserving external labels -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
