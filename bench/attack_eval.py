"""§7.2 H2 자체 T4 — 공격 축 셀별 재현율과 벡터별 오탐.

§7.2 가 이 셋을 이 셋에 걸어 뒀다.

| 지표 | 목표 | 근거 셋 |
|---|---|---|
| L1-ATK 주 헤드 재현율 | **≥ 0.75** | T4 (셀당 100) |
| L1-ATK FPR | **≤ 0.02** | T4 benign 700 |
| 보조 헤드 A(로마자) F1 | ≥ 0.70 | T4′ 200 |

재현율 목표 0.75 의 근거가 §7.1.2 에 있다 — **교사 kanana 의 R 0.748 이 사실상 상한**
이다. 학생이 교사를 넘기 어렵다.

## 셀당 100 은 표본 100 이 아니다

홀드아웃이 작고(공격 131) 변환이 걸리는 문장은 더 적어서 한자 셀은 **13개 문장을
7~8번씩 돌려 쓴다.** §7.2 의 ±0.10 신뢰구간은 독립 100건 가정이므로 그대로 읽으면
구간이 실제보다 좁아 보인다. 그래서 셀별 재현율 옆에 **고유 기저 문장 수**를 같이 낸다.

## 지금은 L0 만 잰다

L1 가중치가 없다. L0 는 §3.2.3 이 정한 대로 **고정밀 문구만** 담당하므로 여기 수치는
낮게 나오는 것이 정상이고, 그 차이가 곧 L1 이 벌어야 할 몫이다 — 낮다고 규칙을 넓히면
정밀도를 잃는데 공격 축의 과탐은 곧 과차단이다(§7.1.1).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from garim.training.data import CorpusRecord

Detector = Callable[[str], bool]

RECALL_TARGET = 0.75
FPR_TARGET = 0.02


@dataclass(slots=True)
class CellScore:
    cell: str
    rows: int = 0
    hits: int = 0
    distinct_bases: int = 0

    @property
    def rate(self) -> float:
        return self.hits / self.rows if self.rows else 0.0


@dataclass(slots=True)
class AttackReport:
    detector: str
    cells: dict[str, CellScore] = field(default_factory=dict)

    def _split(self) -> tuple[list[CellScore], list[CellScore]]:
        attack = [score for cell, score in self.cells.items() if not cell.startswith("NONE:")]
        benign = [score for cell, score in self.cells.items() if cell.startswith("NONE:")]
        return attack, benign

    @property
    def recall(self) -> float:
        attack, _ = self._split()
        rows = sum(score.rows for score in attack)
        return sum(score.hits for score in attack) / rows if rows else 0.0

    @property
    def false_positive_rate(self) -> float:
        _, benign = self._split()
        rows = sum(score.rows for score in benign)
        return sum(score.hits for score in benign) / rows if rows else 0.0

    def as_dict(self) -> dict[str, Any]:
        attack, benign = self._split()
        return {
            "detector": self.detector,
            "recall": round(self.recall, 4),
            "recall_target": RECALL_TARGET,
            "recall_meets_target": self.recall >= RECALL_TARGET,
            "false_positive_rate": round(self.false_positive_rate, 4),
            "fpr_target": FPR_TARGET,
            "fpr_meets_target": self.false_positive_rate <= FPR_TARGET,
            "attack_cells": len(attack),
            "benign_cells": len(benign),
            # ★ 행 수가 아니라 고유 기저 문장 수가 유효 표본이다.
            "by_cell": {
                score.cell: {
                    "rows": score.rows,
                    "rate": round(score.rate, 4),
                    "distinct_bases": score.distinct_bases,
                }
                for score in sorted(self.cells.values(), key=lambda item: item.cell)
            },
        }


def evaluate(records: Sequence[CorpusRecord], detector: Detector, *, name: str) -> AttackReport:
    if not records:
        raise ValueError("attack evaluation needs at least one record")
    report = AttackReport(detector=name)
    bases: dict[str, set[str]] = {}
    for record in records:
        cell = str(record.meta.get("cell", "unknown"))
        score = report.cells.setdefault(cell, CellScore(cell=cell))
        score.rows += 1
        score.hits += bool(detector(record.text))
        bases.setdefault(cell, set()).add(str(record.meta.get("base_id", record.id)))
    for cell, identifiers in bases.items():
        report.cells[cell].distinct_bases = len(identifiers)
    return report


def garim_attack_detector(policy_path: str = "config/policy.yaml") -> Detector:
    """L0 만. L1 가중치가 없으므로 이 값이 곧 규칙 단독 성능이다."""
    from garim import Inspector

    inspector = Inspector(policy_path)

    def detector(text: str) -> bool:
        return bool(inspector.check(text).attacks)

    return detector


def main() -> int:
    import argparse

    from garim.training.data import load_jsonl
    from garim.training.t4 import distinct_bases_per_cell, generate_t4, generate_t4_prime

    parser = argparse.ArgumentParser(description="§7.2 H2 T4 공격 평가")
    parser.add_argument("--holdout", default="data/corpus/attack-synthetic-test.jsonl")
    parser.add_argument("--output", default="artifacts/attack_eval.json")
    args = parser.parse_args()

    holdout = load_jsonl(args.holdout)
    t4 = generate_t4(holdout)
    prime = generate_t4_prime(holdout)
    report = evaluate(t4, garim_attack_detector(), name="garim_l0")
    payload = report.as_dict()

    print(f"T4 {len(t4)}행 · T4′ {len(prime)}행\n")
    print(f"{'셀':<26}{'행':>5}{'재현율':>9}{'고유문장':>9}")
    for cell, score in payload["by_cell"].items():
        print(f"{cell:<26}{score['rows']:>5}{score['rate']:>9.3f}{score['distinct_bases']:>9}")
    print(
        f"\n재현율 {payload['recall']:.3f} (목표 ≥{RECALL_TARGET}) → "
        f"{'통과' if payload['recall_meets_target'] else '미달'}"
    )
    print(
        f"오탐율 {payload['false_positive_rate']:.3f} (목표 ≤{FPR_TARGET}) → "
        f"{'통과' if payload['fpr_meets_target'] else '미달'}"
    )
    thin = {cell: n for cell, n in distinct_bases_per_cell(t4).items() if n < 30}
    if thin:
        print(f"\n⚠️ 고유 기저 문장이 30 미만인 셀 {len(thin)}개 — ±0.10 구간 가정이 깨진다")
        for cell, n in sorted(thin.items(), key=lambda kv: kv[1])[:5]:
            print(f"   {cell}: {n}")
    print("⚠️ L1 가중치가 없다. 이 값은 L0 단독이고 §3.2.3 대로 고정밀 문구만 담당한다.")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
