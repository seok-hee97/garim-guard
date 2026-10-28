#!/usr/bin/env python3
"""Verify and split KDPII before any preprocessing or augmentation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from garim.training.sources import load_source_registry
from garim.training.split import split_kdpii_dialogues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest", default="train/sources.yaml")
    parser.add_argument("--seed", default="garim-kdpii-v1")
    args = parser.parse_args()

    source_path = Path(args.input)
    registry = load_source_registry(args.manifest)
    registry.authorize("KDPII", purpose="train", local_file=source_path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("KDPII root must be a JSON array of dialogues")
    splits = split_kdpii_dialogues(payload, seed=args.seed)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, dialogues in splits.items():
        destination = output_dir / f"{name}.json"
        destination.write_text(json.dumps(dialogues, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{name}: {len(dialogues)} dialogues -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
