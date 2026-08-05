"""Interval-map primitives for lossless source span recovery."""

from __future__ import annotations

Interval = tuple[int, int]
IntervalMap = list[Interval]


def identity_map(length: int) -> IntervalMap:
    return [(index, index + 1) for index in range(length)]


def compose(a: IntervalMap, b: IntervalMap) -> IntervalMap:
    """Compose source-to-middle map ``a`` with middle-to-output map ``b``."""
    output: IntervalMap = []
    for middle_start, middle_end in b:
        if middle_start >= middle_end:
            output.append((0, 0))
            continue
        covered = a[middle_start:middle_end]
        output.append((min(start for start, _ in covered), max(end for _, end in covered)))
    return output


def to_source_span(span: tuple[int, int], interval_map: IntervalMap, source_len: int) -> Interval:
    start, end = span
    if start >= end or not interval_map:
        return (source_len, source_len)
    start = max(0, start)
    end = min(len(interval_map), end)
    if start >= end:
        return (source_len, source_len)
    covered = interval_map[start:end]
    return (min(left for left, _ in covered), max(right for _, right in covered))
