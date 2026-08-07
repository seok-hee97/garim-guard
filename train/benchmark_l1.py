#!/usr/bin/env python3
"""Measure deployable ONNX QInt8 latency, file size, and process RSS."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
from pathlib import Path
from time import perf_counter


def _rss_bytes() -> int:
    output = subprocess.check_output(  # noqa: S603 - fixed executable and arguments
        ["ps", "-o", "rss=", "-p", str(os.getpid())],
        text=True,
    )
    return int(output.strip()) * 1024


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _text_for_tokens(tokenizer, target: int) -> tuple[str, int]:  # noqa: ANN001
    low, high = 1, target * 4
    best = "가"
    best_count = 0
    while low <= high:
        middle = (low + high) // 2
        text = "가 " * middle
        count = len(tokenizer(text, truncation=False)["input_ids"])
        if abs(count - target) < abs(best_count - target) or best_count == 0:
            best, best_count = text, count
        if count < target:
            low = middle + 1
        elif count > target:
            high = middle - 1
        else:
            return text, count
    return best, best_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-file", default="garim_l1.int8.onnx")
    parser.add_argument("--output", required=True)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=100)
    args = parser.parse_args()
    if args.warmup < 0 or args.repeats < 10:
        raise ValueError("warmup must be non-negative and repeats must be at least 10")

    from garim.detect.l1 import OnnxL1Backend
    from garim.normalize.chain import normalize

    model_dir = Path(args.model_dir)
    rss_process_start = _rss_bytes()
    backend = OnnxL1Backend(model_dir, model_filename=args.model_file)
    report = {
        "model": str(model_dir),
        "onnx_bytes": (model_dir / args.model_file).stat().st_size,
        "rss_process_start_bytes": rss_process_start,
        "rss_loaded_bytes": _rss_bytes(),
        "lengths": {},
    }
    for target in (128, 256, 512):
        text, actual_tokens = _text_for_tokens(backend.tokenizer, target)
        bundle = normalize(text)
        for _ in range(args.warmup):
            backend.infer(
                bundle.norm.text,
                normalization_delta=bundle.delta,
                origin="user",
                content_type="prose",
            )
        timings: list[float] = []
        for _ in range(args.repeats):
            started = perf_counter()
            backend.infer(
                bundle.norm.text,
                normalization_delta=bundle.delta,
                origin="user",
                content_type="prose",
            )
            timings.append((perf_counter() - started) * 1000)
        report["lengths"][str(target)] = {
            "actual_tokens": actual_tokens,
            "p50_ms": statistics.median(timings),
            "p95_ms": _percentile(timings, 0.95),
            "mean_ms": statistics.mean(timings),
        }
    report["rss_after_benchmark_bytes"] = _rss_bytes()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"benchmark complete -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
