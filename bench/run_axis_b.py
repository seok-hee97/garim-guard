#!/usr/bin/env python3
"""Run the axis-(b) benchmark for one or more judges and write a comparison report.

    python -m bench.run_axis_b --benchmark data/raw/KoreanGuardrail/benchmark.jsonl \
        --judge garim-l0 --judge rules-no-normalization \
        --output results/axis-b.json

Model judges need their local directory and are slow enough to want `--sample`:

    python -m bench.run_axis_b --benchmark ... --judge kanana \
        --kanana-dir models/pinned/teacher_kanana --sample 200
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench.ko_guardrail import balanced_sample, evaluate, load_benchmark

JUDGES = (
    "garim-l0",
    "rules-no-normalization",
    "kanana",
    "kanana-normalized",
    "kanana-all-streams",
    "gemma4-l2",
    "gemma4-l2-all-streams",
    "gemini",
    "gemini-all-streams",
    "polyguard",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True, help="KoreanGuardrail benchmark.jsonl")
    parser.add_argument("--judge", action="append", choices=JUDGES, required=True)
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--kanana-dir")
    parser.add_argument("--polyguard-dir")
    parser.add_argument("--l2-endpoint", help="Gemma 4 llama-server /v1 (루프백)")
    parser.add_argument("--l2-model", default="gemma-4-e2b-it")
    parser.add_argument("--gemini-model", default="gemini-3.8-flash")
    parser.add_argument("--env", default=".env", help="GEMINI_API_KEY 를 읽을 파일")
    parser.add_argument("--sample", type=int, help="label-balanced subset size")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--output")
    args = parser.parse_args()

    rows = load_benchmark(args.benchmark)
    if args.sample:
        rows = balanced_sample(rows, args.sample, seed=args.seed)

    from bench import judges as judge_module

    reports = []
    for name in args.judge:
        if name == "garim-l0":
            judge = judge_module.garim_l0(args.policy)
        elif name == "rules-no-normalization":
            judge = judge_module.rules_without_normalization()
        elif name.startswith("kanana"):
            if not args.kanana_dir:
                raise SystemExit("--kanana-dir is required for the kanana judge")
            judge = judge_module.kanana_safeguard(args.kanana_dir)
            if name == "kanana-normalized":
                judge = judge_module.with_normalization(judge)
            elif name == "kanana-all-streams":
                judge = judge_module.with_all_streams(judge)
        elif name.startswith("gemini"):
            import os

            from bench.mask_quality import load_env

            key = {**load_env(args.env), **os.environ}.get("GEMINI_API_KEY")
            if not key:
                raise SystemExit("GEMINI_API_KEY 가 없다 — §7.4 대로 유료 키를 쓴다")
            judge = judge_module.gemini(key, args.gemini_model)
            if name == "gemini-all-streams":
                judge = judge_module.with_all_streams(judge)
        elif name.startswith("gemma4-l2"):
            if not args.l2_endpoint:
                raise SystemExit("--l2-endpoint is required for the gemma4-l2 judge")
            judge = judge_module.gemma4_l2(args.l2_endpoint, args.l2_model)
            if name == "gemma4-l2-all-streams":
                judge = judge_module.with_all_streams(judge)
        else:
            if not args.polyguard_dir:
                raise SystemExit("--polyguard-dir is required for the polyguard judge")
            judge = judge_module.polyguard(args.polyguard_dir)
        report = evaluate(rows, judge, name=name)
        reports.append(report.as_dict())
        print(
            f"{name:24} 탐지율 {report.detection_rate:.3f}  "
            f"과방어율 {report.over_defense_rate:.3f}  "
            f"{report.milliseconds_per_row:.1f} ms/행"
        )

    payload = {
        "benchmark": str(Path(args.benchmark).name),
        "rows": len(rows),
        "sampled": bool(args.sample),
        "seed": args.seed if args.sample else None,
        "reports": reports,
    }
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"-> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
