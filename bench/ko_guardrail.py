"""Axis (b) harness over `kimchunsik03/KoreanGuardrail` — DESIGN §6.4.6 회귀 평가셋.

The benchmark reports two rates and the dataset card fixes their definition, so this
module follows it rather than inventing metrics:

    evasion_rate      = 1 - (attack rows the judge flags)      # 낮을수록 좋다
    over_defense_rate = benign rows the judge flags            # 낮을수록 좋다

`technique` maps onto §2.2 은닉 축, which is why this set is worth more to us than a
generic safety benchmark:

    jamo_decompose → A-KO-01   chosung → A-KO-02
    break_spacing  → A-KO-05   zwsp_inject → A-ENC-02
    tensify        → 대응 코드 없음(된소리화)

⚠️ 자작 셋이 아니라는 점이 이 하네스의 값이지만, 피어리뷰 벤치도 아니다 — 개인 저자,
시드는 저자 작성 또는 Anthropic API 생성 후 저자 검수다. 리포트에 병기한다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ATTACK = "attack"
BENIGN = "benign"
Judge = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    id: str
    seed_id: str
    original: str
    text: str
    label: str
    category: str
    technique: str
    intensity: float


@dataclass(slots=True)
class AxisReport:
    judge: str
    rows: int
    attacks: int
    benigns: int
    detection_rate: float
    evasion_rate: float
    over_defense_rate: float
    by_technique: dict[str, float] = field(default_factory=dict)
    by_intensity: dict[str, float] = field(default_factory=dict)
    by_category: dict[str, float] = field(default_factory=dict)
    milliseconds_per_row: float | None = None
    # 판정자가 공격이라 한 행의 id — 두 판정자의 행별 짝(McNemar)은 비율로는 못 낸다.
    flagged_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "rows": self.rows,
            "attacks": self.attacks,
            "benigns": self.benigns,
            "detection_rate": round(self.detection_rate, 4),
            "evasion_rate": round(self.evasion_rate, 4),
            "over_defense_rate": round(self.over_defense_rate, 4),
            "by_technique": {k: round(v, 4) for k, v in sorted(self.by_technique.items())},
            "by_intensity": {k: round(v, 4) for k, v in sorted(self.by_intensity.items())},
            "by_category": {k: round(v, 4) for k, v in sorted(self.by_category.items())},
            "milliseconds_per_row": (
                None if self.milliseconds_per_row is None else round(self.milliseconds_per_row, 2)
            ),
            "flagged_ids": self.flagged_ids,
        }


def load_benchmark(path: str | Path) -> list[BenchmarkRow]:
    """Read `benchmark.jsonl` from the pinned KoreanGuardrail revision."""
    rows: list[BenchmarkRow] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            missing = {"id", "text", "label", "category", "technique", "intensity"} - set(payload)
            if missing:
                raise ValueError(f"{path}:{line_number}: missing fields {sorted(missing)}")
            if payload["label"] not in {ATTACK, BENIGN}:
                raise ValueError(f"{path}:{line_number}: unexpected label {payload['label']!r}")
            rows.append(
                BenchmarkRow(
                    id=str(payload["id"]),
                    seed_id=str(payload.get("seed_id", "")),
                    original=str(payload.get("original", "")),
                    text=str(payload["text"]),
                    label=str(payload["label"]),
                    category=str(payload["category"]),
                    technique=str(payload["technique"]),
                    intensity=float(payload["intensity"]),
                )
            )
    if not rows:
        raise ValueError(f"{path}: benchmark is empty")
    return rows


def _rate(hits: Iterable[bool]) -> float:
    values = list(hits)
    return sum(values) / len(values) if values else 0.0


def _grouped_detection(
    rows: Sequence[BenchmarkRow], flagged: dict[str, bool], key: Callable[[BenchmarkRow], str]
) -> dict[str, float]:
    buckets: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        if row.label == ATTACK:
            buckets[key(row)].append(flagged[row.id])
    return {name: _rate(values) for name, values in buckets.items()}


def evaluate(
    rows: Sequence[BenchmarkRow],
    judge: Judge,
    *,
    name: str,
    timer: Callable[[], float] | None = None,
) -> AxisReport:
    """Run one judge over the benchmark. `judge` returns True when it calls the row an attack."""
    if timer is None:
        from time import perf_counter as timer  # noqa: N813

    started = timer()
    flagged = {row.id: bool(judge(row.text)) for row in rows}
    elapsed_ms = (timer() - started) * 1000

    attacks = [row for row in rows if row.label == ATTACK]
    benigns = [row for row in rows if row.label == BENIGN]
    if not attacks or not benigns:
        raise ValueError("benchmark slice must contain both attack and benign rows")
    detection = _rate(flagged[row.id] for row in attacks)
    return AxisReport(
        judge=name,
        rows=len(rows),
        attacks=len(attacks),
        benigns=len(benigns),
        detection_rate=detection,
        evasion_rate=1.0 - detection,
        over_defense_rate=_rate(flagged[row.id] for row in benigns),
        by_technique=_grouped_detection(rows, flagged, lambda r: r.technique),
        by_intensity=_grouped_detection(rows, flagged, lambda r: f"{r.intensity:g}"),
        by_category=_grouped_detection(rows, flagged, lambda r: r.category),
        milliseconds_per_row=elapsed_ms / len(rows),
        flagged_ids=[row.id for row in rows if flagged[row.id]],
    )


def balanced_sample(
    rows: Sequence[BenchmarkRow], size: int, *, seed: int = 13
) -> list[BenchmarkRow]:
    """Label-balanced subset for judges too slow to run the full 5,555 rows."""
    import random

    shuffler = random.Random(seed)
    attacks = [row for row in rows if row.label == ATTACK]
    benigns = [row for row in rows if row.label == BENIGN]
    shuffler.shuffle(attacks)
    shuffler.shuffle(benigns)
    half = max(1, size // 2)
    return attacks[:half] + benigns[:half]
