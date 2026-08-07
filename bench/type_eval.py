"""S4 — 타입별 분리 FPR + 타입 조건화 ON/OFF 대조. §7.2.2.

★ 이 하네스는 **기능을 자를지 결정하기 위해** 있다.

§8.4-5 가 절삭 순서에 타입 조건화를 넣으면서 조건을 달았다 — *"§7.2.2 의 ON/OFF
대조에서 기여가 측정되지 않으면 먼저 자른다. 측정 결과가 있으면 순위를 3번으로
올린다."* 그리고 §9 가 그것을 차별화 8번으로 세면서 *"기여가 측정되지 않으면
차별화가 아니라 미측정 주장"* 이라고 적었다.

즉 여기서 나오는 숫자가 기능의 존폐를 정한다. 그래서 대조를 **같은 코퍼스·같은
탐지기·정책만 교체**로 좁혔다 — 다른 것이 섞이면 무엇의 기여인지 말할 수 없다.

FPR 분모는 §7.2.1 대로 **음성 세그먼트 수**다.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from garim.api import Inspector
from garim.models import ContentType, Segment
from garim.policy.schema import PolicyConfig, load_policy
from garim.training.data import CorpusRecord

TYPES: tuple[ContentType, ...] = ("prose", "code", "structured", "log")


@dataclass(frozen=True, slots=True)
class TypeCell:
    content_type: str
    negative_segments: int
    false_positive_segments: int
    positive_segments: int
    detected_positive_segments: int

    @property
    def false_positive_rate(self) -> float:
        return (
            self.false_positive_segments / self.negative_segments
            if self.negative_segments
            else 0.0
        )

    @property
    def recall(self) -> float:
        return (
            self.detected_positive_segments / self.positive_segments
            if self.positive_segments
            else 0.0
        )


@dataclass(slots=True)
class TypeReport:
    arm: str
    cells: dict[str, TypeCell]

    @property
    def overall_false_positive_rate(self) -> float:
        negatives = sum(cell.negative_segments for cell in self.cells.values())
        hits = sum(cell.false_positive_segments for cell in self.cells.values())
        return hits / negatives if negatives else 0.0

    @property
    def overall_recall(self) -> float:
        positives = sum(cell.positive_segments for cell in self.cells.values())
        hits = sum(cell.detected_positive_segments for cell in self.cells.values())
        return hits / positives if positives else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "overall_false_positive_rate": round(self.overall_false_positive_rate, 4),
            "overall_recall": round(self.overall_recall, 4),
            "by_type": {
                name: {
                    "false_positive_rate": round(cell.false_positive_rate, 4),
                    "recall": round(cell.recall, 4),
                    "negative_segments": cell.negative_segments,
                    "positive_segments": cell.positive_segments,
                }
                for name, cell in sorted(self.cells.items())
            },
        }


def conditioning_off(config: PolicyConfig) -> PolicyConfig:
    """타입 조건화만 뺀 정책 — 임계·억제·키 제외가 전부 기본값이 된다.

    다른 것은 건드리지 않는다. 임계값이나 코드 모드까지 같이 바뀌면 대조가 무의미해진다.
    """
    return config.model_copy(update={"content_type_policy": {}})


def content_type_of(record: CorpusRecord) -> str:
    """합성 생성기의 `doc_shape` 를 §2.6 타입으로 옮긴다.

    `mixed`·`unknown` 은 별도 셀로 남긴다 — `prose` 로 접으면 §7.2.2 표의 `mixed` 행이
    사라지고, §7.2.1 이 "분할 정확도는 다른 지표" 라며 굳이 나눠둔 구분이 무너진다.
    """
    shape = record.meta.get("doc_shape")
    return str(shape) if isinstance(shape, str) and shape else "unknown"


def evaluate(
    records: Sequence[CorpusRecord], config: PolicyConfig, *, arm: str
) -> TypeReport:
    """정책 하나로 코퍼스를 돌려 타입별 FPR·재현율을 낸다."""
    if not records:
        raise ValueError("type evaluation needs at least one record")
    inspector = Inspector(config)
    negatives: Counter[str] = Counter()
    false_positives: Counter[str] = Counter()
    positives: Counter[str] = Counter()
    detected: Counter[str] = Counter()

    for record in records:
        bucket = content_type_of(record)
        # `mixed`·`unknown` 은 §2.6.2 감지기가 판정하게 둔다 — 여기서 지정하면
        # 감지기 정확도까지 대조에 섞인다.
        declared = bucket if bucket in TYPES else "unknown"
        result = inspector.check([Segment(record.text, content_type=declared)])
        found = bool(result.leaks)
        if record.spans:
            positives[bucket] += 1
            if found:
                detected[bucket] += 1
        else:
            negatives[bucket] += 1
            if found:
                false_positives[bucket] += 1

    names = set(negatives) | set(positives)
    return TypeReport(
        arm=arm,
        cells={
            name: TypeCell(
                content_type=name,
                negative_segments=negatives[name],
                false_positive_segments=false_positives[name],
                positive_segments=positives[name],
                detected_positive_segments=detected[name],
            )
            for name in sorted(names)
        },
    )


def policy_coverage(
    records: Sequence[CorpusRecord], config: PolicyConfig
) -> dict[str, int]:
    """정책이 겨냥하는 케이스가 코퍼스에 **몇 건이나 있는가**.

    ★ 이것이 없으면 `0.0000` 을 잘못 읽는다. §8.4-5 는 "기여가 측정되지 않으면 자른다"
    인데, 코퍼스가 기능을 건드리지도 않아서 0 이 나온 것과 건드렸는데 0 인 것은
    전혀 다른 결론이다. 전자는 **측정 불가**이지 기여 없음이 아니다.

    두 정책 팔에 같은 행을 태워 **판정이 실제로 갈리는 행 수**를 센다. 0 이면 이
    코퍼스로는 이 기능을 판정할 수 없다.
    """
    on_inspector = Inspector(config)
    off_inspector = Inspector(conditioning_off(config))
    differing: Counter[str] = Counter()
    for record in records:
        bucket = content_type_of(record)
        declared = bucket if bucket in TYPES else "unknown"
        segment = [Segment(record.text, content_type=declared)]
        left = {(f.code, f.span) for f in on_inspector.check(segment).leaks}
        right = {(f.code, f.span) for f in off_inspector.check(segment).leaks}
        if left != right:
            differing[bucket] += 1
    return dict(differing)


@dataclass(slots=True)
class ConditioningContrast:
    """§8.4-5 의 판정 재료. ON 이 OFF 보다 나아야 기능이 산다."""

    on: TypeReport
    off: TypeReport
    # 정책이 실제로 판정을 바꾼 행 수. 0 이면 이 코퍼스로는 판정할 수 없다.
    exercised: dict[str, int] = field(default_factory=dict)

    @property
    def measurable(self) -> bool:
        return sum(self.exercised.values()) > 0

    @property
    def verdict(self) -> str:
        """§8.4-5 에 넘길 한 단어. `inconclusive` 를 `cut` 으로 접지 않는다."""
        if not self.measurable:
            return "inconclusive"
        return "keep" if self.contributes else "cut"

    @property
    def false_positive_reduction(self) -> float:
        """OFF 대비 ON 이 줄인 오탐률. 양수여야 기여가 있다."""
        return self.off.overall_false_positive_rate - self.on.overall_false_positive_rate

    @property
    def recall_cost(self) -> float:
        """그 대가로 잃은 재현율. 오탐 감소가 재현율을 깎아서 나온 것이면 기여가 아니다."""
        return self.off.overall_recall - self.on.overall_recall

    @property
    def contributes(self) -> bool:
        return self.false_positive_reduction > 0 and self.recall_cost <= 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "measurable": self.measurable,
            "exercised_rows": dict(sorted(self.exercised.items())),
            "false_positive_reduction": round(self.false_positive_reduction, 4),
            "recall_cost": round(self.recall_cost, 4),
            "contributes": self.contributes,
            "by_type_false_positive_delta": {
                name: round(
                    self.off.cells[name].false_positive_rate
                    - self.on.cells[name].false_positive_rate,
                    4,
                )
                for name in sorted(set(self.on.cells) & set(self.off.cells))
            },
            "on": self.on.as_dict(),
            "off": self.off.as_dict(),
        }


def contrast(
    records: Sequence[CorpusRecord], *, policy_path: str = "config/policy.yaml"
) -> ConditioningContrast:
    config = load_policy(policy_path)
    return ConditioningContrast(
        on=evaluate(records, config, arm="conditioning-on"),
        off=evaluate(records, conditioning_off(config), arm="conditioning-off"),
        exercised=policy_coverage(records, config),
    )


def write_report(path: str | Path, result: ConditioningContrast) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def main() -> int:
    import argparse

    from garim.training.synthetic import generate_pii_corpus

    parser = argparse.ArgumentParser(description="S4 타입 조건화 ON/OFF 대조 (§7.2.2)")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--split", default="test")
    parser.add_argument("--output")
    args = parser.parse_args()

    records = [r for r in generate_pii_corpus() if r.split == args.split][: args.limit]
    result = contrast(records, policy_path=args.policy)
    payload = result.as_dict()

    print(f"{'타입':12} {'FPR(OFF)':>10} {'FPR(ON)':>10} {'감소':>8}")
    for name, delta in payload["by_type_false_positive_delta"].items():
        off = payload["off"]["by_type"][name]["false_positive_rate"]
        on = payload["on"]["by_type"][name]["false_positive_rate"]
        print(f"{name:12} {off:>10.4f} {on:>10.4f} {delta:>+8.4f}")
    print(
        f"\n정책이 판정을 바꾼 행: {sum(payload['exercised_rows'].values())} "
        f"{payload['exercised_rows'] or ''}"
    )
    print(
        f"전체 오탐 감소 {payload['false_positive_reduction']:+.4f} · "
        f"재현율 대가 {payload['recall_cost']:+.4f} · **판정 {payload['verdict']}**"
    )
    if payload["verdict"] == "inconclusive":
        print(
            "  ⚠️ 코퍼스가 이 기능을 건드리지 않는다 — 0.0000 은 '기여 없음' 이 아니라\n"
            "     '측정 불가' 다. §8.4-5 의 절삭 트리거를 여기서 당기면 안 된다."
        )
    if args.output:
        print(f"-> {write_report(args.output, result)}")
    # 0=keep / 1=cut / 2=inconclusive. `inconclusive` 를 `cut` 과 같은 코드로 내지 않는다.
    return {"keep": 0, "cut": 1, "inconclusive": 2}[result.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
