#!/usr/bin/env python3
"""Evaluate a local ONNX L1 artifact on sealed JSONL corpora."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from garim.training.data import load_jsonl
from garim.training.evaluation_seal import verify_evaluation_input_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-file", default="garim_l1.int8.onnx")
    parser.add_argument("--baseline-model-file")
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--evaluation-manifest", default="data/evaluation-input-manifest.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from garim.detect.l1 import OnnxL1Backend
    from garim.training.onnx_evaluation import evaluate_onnx_by_source

    records = [record for path in args.input for record in load_jsonl(path)]
    sealed_contract = verify_evaluation_input_manifest(args.evaluation_manifest, args.input)
    backend = OnnxL1Backend(args.model_dir, model_filename=args.model_file)
    report = evaluate_onnx_by_source(backend, records)
    report["sealed_input_contract"] = sealed_contract
    if args.baseline_model_file:
        baseline = evaluate_onnx_by_source(
            OnnxL1Backend(args.model_dir, model_filename=args.baseline_model_file), records
        )
        report["baseline"] = baseline
        candidate_overall = report["overall"]
        baseline_overall = baseline["overall"]
        report["candidate_minus_baseline"] = {
            "attack_macro_recall": (
                candidate_overall["attack_macro_recall"] - baseline_overall["attack_macro_recall"]
            ),
            "attack_macro_f1": (
                candidate_overall["attack_macro_f1"] - baseline_overall["attack_macro_f1"]
            ),
            "pii_recall_by_label": {
                label: metrics["recall"]
                - baseline_overall["pii_character_by_label"].get(label, {}).get("recall", 0.0)
                for label, metrics in candidate_overall["pii_character_by_label"].items()
            },
        }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"evaluated {len(records)} sealed rows -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
