#!/usr/bin/env python3
"""Seal final test corpora and prove they are disjoint from sealed training inputs."""

from __future__ import annotations

import argparse

from garim.training.evaluation_seal import (
    create_evaluation_input_manifest,
    write_evaluation_input_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument(
        "--training-input-manifest", default="data/training-input-manifest.json"
    )
    parser.add_argument("--labeling-guide", default="docs/LABELING_GUIDE.md")
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--output", default="data/evaluation-input-manifest.json")
    args = parser.parse_args()

    payload = create_evaluation_input_manifest(
        args.input,
        training_manifest_path=args.training_input_manifest,
        provenance_files={
            "labeling_guide": args.labeling_guide,
            "models_manifest": args.models_manifest,
            "sources_manifest": args.sources_manifest,
        },
    )
    destination = write_evaluation_input_manifest(args.output, payload)
    print(
        f"sealed {payload['contract']['records']} final-evaluation rows -> {destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
