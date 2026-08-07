#!/usr/bin/env python3
"""Plan or explicitly download all pinned pre-training assets."""

from __future__ import annotations

import argparse
import json

from garim.training.assets import build_asset_plan, download_assets, inspect_asset_plan
from garim.training.models import load_model_registry
from garim.training.sources import load_source_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--model-root", default="models/pinned")
    parser.add_argument("--data-root", default="data/raw")
    parser.add_argument("--without-teachers", action="store_true")
    parser.add_argument("--without-eval", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform network downloads; without this flag the command is read-only",
    )
    args = parser.parse_args()

    models = load_model_registry(args.models_manifest)
    sources = load_source_registry(args.sources_manifest)
    plan = build_asset_plan(
        models,
        sources,
        model_root=args.model_root,
        data_root=args.data_root,
        include_teachers=not args.without_teachers,
        include_eval=not args.without_eval,
    )
    if not args.execute:
        print(json.dumps(inspect_asset_plan(plan), ensure_ascii=False, indent=2))
        print("dry-run only; pass --execute to download pinned assets")
        return 0
    output = download_assets(models, sources, plan)
    print(f"downloaded and recorded {len(plan)} assets in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
