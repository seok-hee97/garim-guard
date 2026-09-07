#!/usr/bin/env python3
"""Read-only readiness gate for implementation, assets, corpora, and training launch."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any

from garim.training.assets import build_asset_plan, verify_asset_manifest
from garim.training.data import load_jsonl
from garim.training.evaluation_seal import verify_evaluation_input_manifest
from garim.training.models import load_model_registry
from garim.training.provenance import verify_training_input_manifest
from garim.training.readiness import validate_training_corpora
from garim.training.sources import load_source_registry


def _check(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _package(name: str, *, required: bool) -> dict[str, Any]:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return _check(f"package:{name}", not required, "not installed")
    return _check(f"package:{name}", True, version)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=("implementation", "assets", "training"), default="implementation"
    )
    parser.add_argument("--encoder-dir")
    parser.add_argument("--pii-train", action="append", default=[])
    parser.add_argument("--pii-validation", action="append", default=[])
    parser.add_argument("--attack-train", action="append", default=[])
    parser.add_argument("--attack-validation", action="append", default=[])
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--asset-manifest", default="data/asset-manifest.json")
    parser.add_argument("--config", default="train/training.yaml")
    parser.add_argument(
        "--input-manifest", default="data/training-input-manifest.json"
    )
    parser.add_argument(
        "--evaluation-manifest", default="data/evaluation-input-manifest.json"
    )
    args = parser.parse_args()

    results = [
        _check(
            "python",
            sys.version_info[:2] == (3, 11),
            {"version": platform.python_version(), "required": "3.11.x"},
        ),
        _check("uv.lock", Path("uv.lock").is_file(), "dependency lock must be committed"),
    ]
    required_files = (
        "train/download_assets.py",
        "train/prepare_corpora.py",
        "train/apply_translations.py",
        "train/run_teacher.py",
        "train/resolve_teacher_labels.py",
        "train/apply_review_labels.py",
        "train/create_labeling_queue.py",
        "train/apply_eval_labels.py",
        "train/apply_external_eval_labels.py",
        "train/finalize_training_inputs.py",
        "train/finalize_evaluation_inputs.py",
        "train/merge_corpora.py",
        "train/train_l1.py",
        "train/export_l1.py",
        "train/evaluate_l1.py",
        "train/benchmark_l1.py",
        "train/training.yaml",
        "train/MODEL_CARD_TEMPLATE.md",
        "docs/LABELING_GUIDE.md",
    )
    results.extend(
        _check(f"file:{path}", Path(path).is_file(), path) for path in required_files
    )
    try:
        model_registry = load_model_registry(args.models_manifest)
        source_registry = load_source_registry(args.sources_manifest)
        results.append(_check("manifests", True, "models and sources parsed"))
    except ValueError as exc:
        results.append(_check("manifests", False, str(exc)))
        model_registry = source_registry = None

    if args.stage in {"assets", "training"} and model_registry and source_registry:
        plan = build_asset_plan(model_registry, source_registry)
        status = verify_asset_manifest(args.asset_manifest, plan)
        results.append(
            _check(
                "pinned-assets",
                all(item["ok"] for item in status),
                status,
            )
        )

    if args.stage == "training":
        for package in ("torch", "transformers", "datasets", "onnx", "onnxruntime"):
            results.append(_package(package, required=True))
        encoder = Path(args.encoder_dir) if args.encoder_dir else None
        results.append(
            _check(
                "local-encoder",
                bool(encoder and encoder.is_dir() and (encoder / "config.json").is_file()),
                str(encoder) if encoder else "--encoder-dir is required",
            )
        )
        corpus_groups = {
            "pii-train": args.pii_train,
            "pii-validation": args.pii_validation,
            "attack-train": args.attack_train,
            "attack-validation": args.attack_validation,
        }
        for name, paths in corpus_groups.items():
            try:
                count = sum(len(load_jsonl(path)) for path in paths)
                ok = bool(paths) and count > 0
                detail: Any = {"paths": paths, "records": count}
            except (OSError, ValueError) as exc:
                ok, detail = False, str(exc)
            results.append(_check(f"corpus:{name}", ok, detail))
        try:
            corpus_contract = validate_training_corpora(
                [record for path in args.pii_train for record in load_jsonl(path)],
                [record for path in args.pii_validation for record in load_jsonl(path)],
                [record for path in args.attack_train for record in load_jsonl(path)],
                [record for path in args.attack_validation for record in load_jsonl(path)],
            )
            results.append(_check("training-corpora-contract", True, corpus_contract))
        except (OSError, ValueError) as exc:
            results.append(_check("training-corpora-contract", False, str(exc)))
        try:
            sealed_contract = verify_training_input_manifest(
                args.input_manifest,
                {
                    "pii_train": args.pii_train,
                    "pii_validation": args.pii_validation,
                    "attack_train": args.attack_train,
                    "attack_validation": args.attack_validation,
                },
                provenance_files={
                    "asset_manifest": args.asset_manifest,
                    "models_manifest": args.models_manifest,
                    "sources_manifest": args.sources_manifest,
                    "training_config": args.config,
                },
            )
            results.append(_check("sealed-training-inputs", True, sealed_contract))
        except (OSError, ValueError) as exc:
            results.append(_check("sealed-training-inputs", False, str(exc)))
        try:
            evaluation_contract = verify_evaluation_input_manifest(
                args.evaluation_manifest
            )
            results.append(
                _check("sealed-evaluation-inputs", True, evaluation_contract)
            )
        except (OSError, ValueError) as exc:
            results.append(_check("sealed-evaluation-inputs", False, str(exc)))

    ok = all(result["ok"] for result in results)
    print(
        json.dumps(
            {"ok": ok, "stage": args.stage, "checks": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
