#!/usr/bin/env python3
"""Seal fully translated and labeled train/validation inputs before optimizer creation."""

from __future__ import annotations

import argparse
import json

from garim.training.assets import build_asset_plan, verify_asset_manifest
from garim.training.models import load_model_registry
from garim.training.provenance import (
    create_training_input_manifest,
    write_training_input_manifest,
)
from garim.training.sources import load_source_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pii-train", action="append", required=True)
    parser.add_argument("--pii-validation", action="append", required=True)
    parser.add_argument("--attack-train", action="append", required=True)
    parser.add_argument("--attack-validation", action="append", required=True)
    parser.add_argument("--asset-manifest", default="data/asset-manifest.json")
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--config", default="train/training.yaml")
    parser.add_argument("--output", default="data/training-input-manifest.json")
    args = parser.parse_args()

    models = load_model_registry(args.models_manifest)
    sources = load_source_registry(args.sources_manifest)
    asset_status = verify_asset_manifest(args.asset_manifest, build_asset_plan(models, sources))
    failed = [item for item in asset_status if not item["ok"]]
    if failed:
        raise ValueError(f"cannot seal inputs while pinned assets have drift: {failed}")

    paths = {
        "pii_train": args.pii_train,
        "pii_validation": args.pii_validation,
        "attack_train": args.attack_train,
        "attack_validation": args.attack_validation,
    }
    provenance_files = {
        "asset_manifest": args.asset_manifest,
        "models_manifest": args.models_manifest,
        "sources_manifest": args.sources_manifest,
        "training_config": args.config,
    }
    payload = create_training_input_manifest(paths, provenance_files=provenance_files)
    output = write_training_input_manifest(args.output, payload)
    print(
        json.dumps(
            {
                "sealed": True,
                "output": str(output),
                "records": payload["contract"]["files"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
