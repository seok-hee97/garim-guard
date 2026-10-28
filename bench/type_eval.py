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
            self.false_positive_segments / self.negative_segments if self.negative_segments else 0.0
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


def evaluate(records: Sequence[CorpusRecord], config: PolicyConfig, *, arm: str) -> TypeReport:
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


def policy_coverage(records: Sequence[CorpusRecord], config: PolicyConfig) -> dict[str, int]:
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

    # 교환비 임계. 오탐 1 을 줄이려고 재현율 1 을 내주면 남는 것이 없다.
    MIN_TRADE_RATIO = 1.0

    @property
    def verdict(self) -> str:
        """§8.4-5 에 넘길 한 단어. `inconclusive` 를 `cut` 으로 접지 않는다.

        keep   기여가 있고 교환비가 임계 이상
        review 기여는 있으나 재현율 대가가 그만큼 크다 — 기제별로 갈라 봐야 한다
        cut    기여가 없다
        """
        if not self.measurable:
            return "inconclusive"
        if not self.contributes:
            return "cut"
        return "keep" if self.trade_ratio >= self.MIN_TRADE_RATIO else "review"

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
        """기여가 **있는가**. 값어치가 있는가는 `trade_ratio` 가 따로 답한다."""
        return self.false_positive_reduction > 0

    @property
    def trade_ratio(self) -> float:
        """줄인 오탐 ÷ 잃은 재현율. 대가가 없으면 `inf`.

        ★ 초판은 `recall_cost <= 0` 을 기여의 조건으로 걸었다. 그러면 오탐을 0.049
        줄이고 재현율을 0.014 내준 결과가 `cut` 으로 나온다 — 유리한 교환을 기여
        없음으로 접는 것이라 §8.4-5 가 물은 것("기여가 있는가")에 답하지 못한다.
        불리언 하나에 숨은 임계를 넣지 않고 비율을 드러낸다.
        """
        if self.recall_cost <= 0:
            return float("inf")
        return self.false_positive_reduction / self.recall_cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "measurable": self.measurable,
            "exercised_rows": dict(sorted(self.exercised.items())),
            "false_positive_reduction": round(self.false_positive_reduction, 4),
            "recall_cost": round(self.recall_cost, 4),
            "trade_ratio": (
                None if self.trade_ratio == float("inf") else round(self.trade_ratio, 2)
            ),
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


@dataclass(frozen=True, slots=True)
class Mechanism:
    """정책 한 칸 — (타입, 필드). 절삭은 이 단위로 결정된다."""

    content_type: str
    field: str

    def __str__(self) -> str:
        return f"{self.content_type}.{self.field}"


def active_mechanisms(config: PolicyConfig) -> list[Mechanism]:
    """기본값과 다른 칸만 고른다 — 켜져 있지도 않은 것을 판정하지 않는다."""
    from garim.policy.schema import ContentPolicy

    default = ContentPolicy()
    return [
        Mechanism(name, field)
        for name, policy in sorted(config.content_type_policy.items())
        for field in type(policy).model_fields
        if getattr(policy, field) != getattr(default, field)
    ]


def without_mechanism(config: PolicyConfig, mechanism: Mechanism) -> PolicyConfig:
    """그 칸 하나만 기본값으로 되돌린 정책."""
    from garim.policy.schema import ContentPolicy

    policy = config.content_type_policy[mechanism.content_type]
    reverted = policy.model_copy(
        update={mechanism.field: getattr(ContentPolicy(), mechanism.field)}
    )
    return config.model_copy(
        update={
            "content_type_policy": {**config.content_type_policy, mechanism.content_type: reverted}
        }
    )


def per_mechanism(
    records: Sequence[CorpusRecord], *, policy_path: str = "config/policy.yaml"
) -> dict[str, dict[str, Any]]:
    """★ leave-one-out 절삭 판정.

    전체 ON/OFF 는 **합계가 기제 하나의 손해를 가린다.** 실제로 그랬다 — `log` 의
    큰 이득이 `code` 델타의 순손해를 덮어 전체 판정이 `keep` 으로 나왔고, 그러면
    "타입 조건화를 유지한다" 가 세 기제 전부를 유지한다는 뜻이 되어버린다.

    각 칸을 하나씩 되돌려 "이걸 빼면 무슨 일이 일어나는가" 를 묻는다 — 절삭 결정이
    실제로 답해야 하는 질문이다.
    """
    config = load_policy(policy_path)
    full = evaluate(records, config, arm="full")
    verdicts: dict[str, dict[str, Any]] = {}
    for mechanism in active_mechanisms(config):
        stripped = without_mechanism(config, mechanism)
        reduced = evaluate(records, stripped, arm=f"without:{mechanism}")
        # 이 칸을 **빼면** 어떻게 되는가. 부호를 한 번 뒤집으므로 주석으로 못박는다.
        #   fpr_gain > 0    빼면 오탐이 늘어난다  → 이 칸이 오탐을 줄이고 있었다(이득)
        #   recall_gain > 0 빼면 재현율이 오른다  → 이 칸이 재현율을 깎고 있었다(대가)
        fpr_gain = reduced.overall_false_positive_rate - full.overall_false_positive_rate
        recall_gain = reduced.overall_recall - full.overall_recall
        if fpr_gain == 0 and recall_gain == 0:
            verdict = "inconclusive"  # 이 칸은 이 코퍼스에서 아무 판정도 바꾸지 않았다
        elif fpr_gain <= 0 and recall_gain > 0:
            # 빼면 재현율이 오르고 오탐은 안 는다 — 이 칸은 순손해다.
            verdict = "cut"
        elif fpr_gain > 0 and recall_gain <= 0:
            verdict = "keep"  # 빼면 오탐만 늘어난다 — 공짜 이득이다
        elif fpr_gain > 0 and fpr_gain >= recall_gain:
            verdict = "keep"  # 대가가 있지만 이득이 그보다 크다
        else:
            verdict = "review"
        verdicts[str(mechanism)] = {
            "verdict": verdict,
            "false_positive_contribution": round(fpr_gain, 4),
            "recall_contribution": round(-recall_gain, 4),
        }
    return verdicts


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
    parser.add_argument(
        "--per-mechanism",
        action="store_true",
        help="정책 칸 하나씩 되돌려 절삭을 판정한다 — 합계는 기제 하나의 손해를 가린다",
    )
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
        f"재현율 대가 {payload['recall_cost']:+.4f} · "
        f"교환비 {payload['trade_ratio'] if payload['trade_ratio'] is not None else '∞'} · "
        f"**판정 {payload['verdict']}**"
    )
    if payload["verdict"] == "inconclusive":
        print(
            "  ⚠️ 코퍼스가 이 기능을 건드리지 않는다 — 0.0000 은 '기여 없음' 이 아니라\n"
            "     '측정 불가' 다. §8.4-5 의 절삭 트리거를 여기서 당기면 안 된다."
        )
    if args.per_mechanism:
        print(f"\n{'기제':34} {'오탐 기여':>10} {'재현율 기여':>12} {'판정':>12}")
        for name, row in per_mechanism(records, policy_path=args.policy).items():
            print(
                f"{name:34} {row['false_positive_contribution']:>+10.4f} "
                f"{row['recall_contribution']:>+12.4f} {row['verdict']:>12}"
            )
    if args.output:
        print(f"-> {write_report(args.output, result)}")
    # 0=keep / 1=cut / 2=inconclusive. `inconclusive` 를 `cut` 과 같은 코드로 내지 않는다.
    return {"keep": 0, "cut": 1, "inconclusive": 2, "review": 3}[result.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
