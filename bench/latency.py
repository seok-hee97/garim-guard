"""§7.1.3 지연 예산 검증 — 프로파일·입력 길이·세그먼트 수별 곡선.

§7.6-5 가 *"지연은 하드웨어·입력 길이 선언 후. 사양 없는 지연 수치는 보고하지
않는다 — 예산으로 잡을 때도 마찬가지다"* 라고 했으므로 이 하네스는 환경을 함께
기록하지 않으면 리포트를 만들지 않는다.

§7.1.3 의 예산은 **다중 세그먼트 기준**이다(p50 300ms / p95 700ms). 세그먼트 하나로
재고 그 수치를 예산과 비교하면 안 된다 — 그래서 세그먼트 수를 축으로 둔다.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from garim import Inspector, Segment

# §7.1.3 다중 세그먼트 예산.
BUDGET_P50_MS = 300.0
BUDGET_P95_MS = 700.0


@dataclass(frozen=True, slots=True)
class LatencyPoint:
    label: str
    segments: int
    characters: int
    runs: int
    p50_ms: float
    p95_ms: float
    max_ms: float

    @property
    def within_budget(self) -> bool:
        return self.p50_ms <= BUDGET_P50_MS and self.p95_ms <= BUDGET_P95_MS

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "segments": self.segments,
            "characters": self.characters,
            "runs": self.runs,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "within_budget": self.within_budget,
        }


def environment() -> dict[str, Any]:
    """§7.6-5 — 이 블록 없이는 수치를 내보내지 않는다."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "l1_backend": None,  # 가중치가 붙으면 여기에 이름과 revision 이 들어간다
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def measure(
    inspector: Inspector,
    segments: Sequence[Segment],
    *,
    label: str,
    runs: int = 30,
    warmup: int = 3,
    mode: str = "interactive",
) -> LatencyPoint:
    if runs < 1:
        raise ValueError("runs must be positive")
    body = list(segments)
    for _ in range(warmup):
        inspector.check(body, mode=mode)
    samples: list[float] = []
    for _ in range(runs):
        started = perf_counter()
        inspector.check(body, mode=mode)
        samples.append((perf_counter() - started) * 1000)
    return LatencyPoint(
        label=label,
        segments=len(body),
        characters=sum(len(item.text) for item in body),
        runs=runs,
        p50_ms=statistics.median(samples),
        p95_ms=_percentile(samples, 0.95),
        max_ms=max(samples),
    )


_FILLER = "고객 문의 내용을 확인해 주세요. 연락처는 010-1234-5678 이고 메일은 a@b.test 입니다. "


def default_grid(inspector: Inspector, *, runs: int = 30) -> list[LatencyPoint]:
    """입력 길이 × 세그먼트 수. §7.1.3 이 예산을 다중 세그먼트로 잡았으므로 둘 다 축이다."""
    points: list[LatencyPoint] = []
    for characters in (256, 1024, 4096):
        text = (_FILLER * (characters // len(_FILLER) + 1))[:characters]
        points.append(measure(inspector, [Segment(text)], label=f"1seg/{characters}c", runs=runs))
    for count in (3, 8, 16):
        chunk = (_FILLER * 4)[:512]
        body = [Segment(chunk, origin="user") for _ in range(count)]
        points.append(measure(inspector, body, label=f"{count}seg/512c", runs=runs))
    return points


def write_report(path: str | Path, points: Sequence[LatencyPoint]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "environment": environment(),
                "budget_ms": {"p50": BUDGET_P50_MS, "p95": BUDGET_P95_MS},
                "points": [item.as_dict() for item in points],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="§7.1.3 지연 곡선")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--output")
    args = parser.parse_args()

    inspector = Inspector(args.policy)
    points = default_grid(inspector, runs=args.runs)
    info = environment()
    print(f"환경: {info['platform']} / {info['machine']} / python {info['python']}")
    print(f"L1 백엔드: {info['l1_backend'] or '없음 (P0 프로파일)'}")
    print(f"{'구성':16} {'p50(ms)':>9} {'p95(ms)':>9} {'예산':>6}")
    for point in points:
        mark = "OK" if point.within_budget else "초과"
        print(f"{point.label:16} {point.p50_ms:>9.2f} {point.p95_ms:>9.2f} {mark:>6}")
    if args.output:
        print(f"-> {write_report(args.output, points)}")
    return 0 if all(point.within_budget for point in points) else 1


if __name__ == "__main__":
    sys.exit(main())
