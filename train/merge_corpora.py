#!/usr/bin/env python3
"""Merge compatible JSONL shards while removing exact train/eval text leakage."""

from __future__ import annotations

import argparse

from garim.training.data import load_jsonl, validate_corpus, write_jsonl
from garim.training.split import fingerprint_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--blocked-eval", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--task", choices=("pii", "attack"), required=True)
    parser.add_argument("--split", choices=("train", "validation", "test"), required=True)
    parser.add_argument("--require-labels", action="store_true")
    args = parser.parse_args()

    blocked = {
        fingerprint_text(record.text)
        for path in args.blocked_eval
        for record in load_jsonl(path)
    }
    seen: set[str] = set()
    merged = []
    dropped_eval = 0
    dropped_duplicate = 0
    for path in args.input:
        for record in load_jsonl(path):
            if record.task != args.task or record.split != args.split:
                raise ValueError(f"{record.id}: incompatible task/split in {path}")
            if args.require_labels and record.task == "attack" and record.attack_label is None:
                raise ValueError(f"{record.id}: unresolved attack label")
            fingerprint = fingerprint_text(record.text)
            if fingerprint in blocked:
                dropped_eval += 1
                continue
            if fingerprint in seen:
                dropped_duplicate += 1
                continue
            seen.add(fingerprint)
            merged.append(record)
    validate_corpus(merged)
    write_jsonl(args.output, merged)
    print(
        f"merged={len(merged)} dropped_eval={dropped_eval} "
        f"dropped_duplicate={dropped_duplicate} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
