#!/usr/bin/env python3
"""KoreanGuardrail 위 두 판정자의 행별 짝 비교 — McNemar 정확 검정(PREREG_L1_R2 §5).

탐지율 차이만으로는 두 모델이 같은 행을 놓쳤는지 다른 행을 놓쳤는지 모른다. `run_axis_b` 산출물의
`flagged_ids` 로 공격 행 · 정상 행 각각에서 한쪽만 맞힌 행 수(b, c)를 세고, 양측 정확 p 값을 낸다.

    python -m bench.mcnemar_axis_b --benchmark data/raw/KoreanGuardrail/benchmark.jsonl \\
      --baseline train/artifacts/r2/l1-onnx-axis-b.json \\
      --candidate train/artifacts/r2/l1-r2-onnx-axis-b.json
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path
from typing import Any

from bench.ko_guardrail import ATTACK, BENIGN, load_benchmark


def exact_mcnemar(only_baseline: int, only_candidate: int) -> float:
    """불일치 쌍 b · c 에 대한 양측 정확 p(이항 0.5)."""
    n = only_baseline + only_candidate
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(only_baseline, only_candidate) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _flagged(path: Path, judge: str | None) -> tuple[str, set[str]]:
    reports = json.loads(path.read_text(encoding="utf-8"))["reports"]
    if judge is not None:
        reports = [report for report in reports if report["judge"] == judge]
    if len(reports) != 1:
        raise SystemExit(f"{path}: 판정자가 하나여야 한다 — --judge 로 고른다")
    if "flagged_ids" not in reports[0]:
        raise SystemExit(f"{path}: flagged_ids 가 없다 — 행별 판정을 남기기 전 산출물이다")
    return reports[0]["judge"], set(reports[0]["flagged_ids"])


def compare(rows: list[Any], baseline: set[str], candidate: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label in (ATTACK, BENIGN):
        ids = [row.id for row in rows if row.label == label]
        only_baseline = sum(1 for i in ids if i in baseline and i not in candidate)
        only_candidate = sum(1 for i in ids if i in candidate and i not in baseline)
        result[label] = {
            "rows": len(ids),
            "baseline_flagged": sum(1 for i in ids if i in baseline),
            "candidate_flagged": sum(1 for i in ids if i in candidate),
            "only_baseline": only_baseline,
            "only_candidate": only_candidate,
            "exact_p": exact_mcnemar(only_baseline, only_candidate),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--judge", help="산출물에 판정자가 여럿일 때")
    parser.add_argument("--output")
    args = parser.parse_args()

    rows = load_benchmark(args.benchmark)
    baseline_name, baseline = _flagged(Path(args.baseline), args.judge)
    candidate_name, candidate = _flagged(Path(args.candidate), args.judge)
    known = {row.id for row in rows}
    if not (baseline | candidate) <= known:
        raise SystemExit("flagged_ids 에 벤치마크에 없는 id 가 있다 — 다른 셋의 산출물이다")
    payload = {
        "benchmark": Path(args.benchmark).name,
        "baseline": {"file": args.baseline, "judge": baseline_name},
        "candidate": {"file": args.candidate, "judge": candidate_name},
        "by_label": compare(rows, baseline, candidate),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
