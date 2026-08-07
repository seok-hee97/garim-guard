#!/usr/bin/env python3
"""Validate source manifest structure without downloading any dataset."""

from __future__ import annotations

import argparse

from garim.training.sources import load_source_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="train/sources.yaml")
    args = parser.parse_args()
    registry = load_source_registry(args.manifest)
    for source in registry.manifest.sources:
        if source.enabled and source.hf_id is not None and source.revision is None:
            raise ValueError(f"{source.name}: missing pinned revision")
    print(f"OK: {len(registry.manifest.sources)} registered sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
