"""S4 타입 조건화 대조 — §8.4-5 의 절삭 판정을 만들어내는 하네스.

여기서 지키는 것 하나: **`inconclusive` 를 `cut` 으로 접지 않는다.** 코퍼스가 기능을
건드리지도 않아서 0 이 나온 것과, 건드렸는데 0 인 것은 전혀 다른 결론이다.
"""

from __future__ import annotations

import pytest

from bench.type_eval import (
    ConditioningContrast,
    TypeCell,
    TypeReport,
    conditioning_off,
    content_type_of,
    contrast,
    policy_coverage,
)
from garim.policy.schema import load_policy
from garim.training.data import CorpusRecord, CorpusSpan


def _record(record_id: str, text: str, shape: str, *spans: tuple[int, int, str]) -> CorpusRecord:
    return CorpusRecord(
        id=record_id,
        task="pii",
        text=text,
        source="fixture",
        split="test",
        spans=[CorpusSpan(start=s, end=e, label=label) for s, e, label in spans],
        meta={"doc_shape": shape},
    )


def _report(
    arm: str, negatives: int, false_positives: int, positives: int, hits: int
) -> TypeReport:
    return TypeReport(
        arm=arm,
        cells={
            "prose": TypeCell(
                content_type="prose",
                negative_segments=negatives,
                false_positive_segments=false_positives,
                positive_segments=positives,
                detected_positive_segments=hits,
            )
        },
    )


def test_conditioning_off_removes_only_the_type_policy() -> None:
    """다른 것이 같이 바뀌면 무엇의 기여인지 말할 수 없다."""
    config = load_policy("config/policy.yaml")
    stripped = conditioning_off(config)
    assert config.content_type_policy and not stripped.content_type_policy
    assert stripped.thresholds == config.thresholds
    assert stripped.codes == config.codes
    assert stripped.limits == config.limits


def test_mixed_and_unknown_keep_their_own_cells() -> None:
    """§7.2.2 표의 `mixed` 행이 `prose` 로 접히면 안 된다."""
    assert content_type_of(_record("a", "x", "mixed")) == "mixed"
    assert content_type_of(_record("b", "x", "unknown")) == "unknown"
    bare = CorpusRecord(id="c", task="pii", text="x", source="f", split="test")
    assert content_type_of(bare) == "unknown"


def test_unexercised_policy_reports_inconclusive_not_cut() -> None:
    """★ 0.0000 의 두 원인을 가른다 — 이것이 이 하네스의 핵심 장치다."""
    same = _report("on", 10, 1, 10, 9)
    result = ConditioningContrast(on=same, off=_report("off", 10, 1, 10, 9), exercised={})
    assert not result.measurable
    assert result.verdict == "inconclusive"
    assert result.as_dict()["verdict"] == "inconclusive"


def test_exercised_policy_that_helps_is_kept() -> None:
    result = ConditioningContrast(
        on=_report("on", 100, 5, 100, 90),
        off=_report("off", 100, 20, 100, 90),
        exercised={"code": 15},
    )
    assert result.measurable
    assert result.false_positive_reduction == pytest.approx(0.15)
    assert result.recall_cost == 0.0
    assert result.verdict == "keep"


def test_exercised_policy_that_only_costs_recall_is_cut() -> None:
    """오탐을 못 줄이면서 재현율만 깎으면 기여가 아니다."""
    result = ConditioningContrast(
        on=_report("on", 100, 20, 100, 85),
        off=_report("off", 100, 20, 100, 90),
        exercised={"code": 23},
    )
    assert result.recall_cost == pytest.approx(0.05)
    assert not result.contributes
    assert result.verdict == "cut"


def test_favourable_trade_is_kept_not_cut() -> None:
    """★ 유리한 교환을 `cut` 으로 접지 않는다.

    초판은 `recall_cost <= 0` 을 기여의 조건으로 걸어서, 오탐 0.049 를 줄이고 재현율
    0.014 를 내준 실측 결과가 `cut` 으로 나왔다. §8.4-5 가 물은 것은 "기여가 있는가"
    이지 "대가가 0 인가" 가 아니다.
    """
    result = ConditioningContrast(
        on=_report("on", 100, 5, 100, 86),
        off=_report("off", 100, 20, 100, 90),
        exercised={"log": 56},
    )
    assert result.contributes
    assert result.trade_ratio == pytest.approx(0.15 / 0.04)
    assert result.verdict == "keep"


def test_unfavourable_trade_asks_for_review_not_a_silent_keep() -> None:
    """오탐 1 을 줄이려고 재현율 1 이상을 내주면 남는 것이 없다."""
    result = ConditioningContrast(
        on=_report("on", 100, 18, 100, 80),
        off=_report("off", 100, 20, 100, 90),
        exercised={"code": 30},
    )
    assert result.contributes
    assert result.trade_ratio < 1.0
    assert result.verdict == "review"


def test_coverage_counts_rows_where_the_arms_actually_disagree() -> None:
    config = load_policy("config/policy.yaml")
    rows = [
        # log 의 `suppress: [L-CM-04]` 가 걸리는 행 — 두 팔의 판정이 갈린다.
        _record("log1", "2026-08-08 INFO client=192.168.0.10 ok", "log"),
        # 어느 정책도 건드리지 않는 행.
        _record("p1", "배송이 언제 되나요?", "prose"),
    ]
    coverage = policy_coverage(rows, config)
    assert coverage.get("log") == 1
    assert "prose" not in coverage


def test_contrast_on_a_corpus_that_exercises_the_policy() -> None:
    rows = [
        _record("log1", "2026-08-08 INFO client=192.168.0.10 ok", "log"),
        _record("log2", "2026-08-08 WARN peer=10.0.0.7 retry", "log"),
        _record("p1", "메일 a@b.test 입니다", "prose", (3, 11, "EMAIL")),
    ]
    result = contrast(rows)
    assert result.measurable
    # log 의 IP 억제가 음성 오탐을 줄인다 — 기여가 있는 방향이다.
    assert result.false_positive_reduction > 0
    assert result.verdict == "keep"
