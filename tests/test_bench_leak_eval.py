"""H1 하네스 — §7.6-2 "재현율 옆에 항상 FPR" 을 구조로 강제하는지 본다."""

from __future__ import annotations

import pytest

from bench.leak_eval import evaluate, garim_leak_detector
from garim.training.data import CorpusRecord, CorpusSpan


def _record(record_id: str, text: str, *spans: tuple[int, int, str]) -> CorpusRecord:
    return CorpusRecord(
        id=record_id,
        task="pii",
        text=text,
        source="fixture",
        split="test",
        spans=[CorpusSpan(start=s, end=e, label=label) for s, e, label in spans],
    )


ROWS = [
    _record("p1", "홍길동 010-1234-5678", (4, 17, "PHONE")),
    _record("p2", "메일 a@b.test 입니다", (3, 11, "EMAIL")),
    _record("n1", "배송이 언제 되나요?"),
    _record("n2", "영업시간을 알려주세요"),
]


def test_report_cannot_hide_the_false_positive_rate() -> None:
    """재현율만 담을 수 있는 형태였으면 §7.6-2 를 코드가 지키지 못한다."""
    report = evaluate(ROWS, lambda t: [], name="never")
    payload = report.as_dict()
    assert "segment_false_positive_rate" in payload
    assert payload["segment_false_positive_rate"] == 0.0
    assert payload["negative_segments"] == 2


def test_false_positive_denominator_is_segments_not_spans() -> None:
    """§7.2.1 — 한 세그먼트에 오탐이 셋 나도 그 세그먼트 하나가 오탐이다."""
    noisy = evaluate(
        ROWS,
        lambda t: [(0, 1, "PHONE"), (1, 2, "EMAIL"), (2, 3, "CARD")],
        name="noisy",
    )
    assert noisy.negative_segments == 2
    assert noisy.false_positive_segments == 2
    assert noisy.segment_false_positive_rate == pytest.approx(1.0)


def test_character_level_scoring_credits_partial_overlap() -> None:
    """스팬 완전일치는 경계 한 글자 차이로 0점이 된다 — 문자 단위라야 손해와 비례한다."""
    partial = evaluate([ROWS[0]], lambda t: [(4, 10, "PHONE")], name="partial")
    phone = partial.by_label["PHONE"]
    assert phone.gold_characters == 13
    assert phone.true_positive == 6
    assert phone.precision == pytest.approx(1.0)
    assert 0.4 < phone.recall < 0.5


def test_wrong_label_on_the_right_span_is_not_a_hit() -> None:
    mislabeled = evaluate([ROWS[0]], lambda t: [(4, 17, "CARD")], name="mislabeled")
    assert mislabeled.by_label["PHONE"].true_positive == 0
    assert mislabeled.by_label["CARD"].precision == 0.0


def test_empty_input_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one record"):
        evaluate([], lambda t: [], name="empty")


def test_garim_detector_reports_only_maskable_leaks() -> None:
    """`L-SEC-*`·`L-DOC-01` 은 마스킹 라벨이 없어 H1 대상이 아니다."""
    detector = garim_leak_detector()
    assert detector("메일 hong@example.test 입니다")
    assert not detector("대외비\n\n사업계획 요약")


def test_garim_detector_finds_the_synthetic_positives() -> None:
    report = evaluate(ROWS, garim_leak_detector(), name="garim-l0")
    assert report.by_label["PHONE"].recall == pytest.approx(1.0)
    assert report.by_label["EMAIL"].recall == pytest.approx(1.0)
    assert report.macro_f1 > 0.9
