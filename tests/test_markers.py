"""Regression cover for the shipped confidentiality-marker recognizer.

`docs/probes/marker_probe.py` validates the §2.5 *scheme* with its own reference implementation,
so it kept reporting 100%/0% while `garim/detect/rules/markers.py` sat at 60%/40% with 22
of the 25 dictionary terms missing. These tests call the shipped code.
"""

from __future__ import annotations

import pytest

from garim import Inspector
from garim.detect.rules.markers import _MARKER_TERMS, detect_markers

# The exact cases DESIGN §2.5 measured (부록 A.5).
DESIGN_CASES = [
    ("대외비\n\n2026년 3분기 사업계획", True),
    ("「대외비」\n첨부: 원가 분석표", True),
    ("보안등급: 기밀\n프로젝트 개요", True),
    ("CONFIDENTIAL\nQ3 Financial Summary", True),
    ("영업비밀\n제조 공정 상세", True),
    ("이거 대외비인데 물어봐도 될까요?", False),
    ("그 자료 기밀이라서 공유가 어렵습니다", False),
    ("대외비 처리 절차가 궁금합니다", False),
    ("기밀 유지 서약서 양식을 찾고 있어요", False),
    ("일반 문의드립니다. 배송이 언제 되나요?", False),
]


@pytest.mark.parametrize(("text", "expected"), DESIGN_CASES)
def test_shipped_recognizer_reproduces_the_design_measurement(text: str, expected: bool) -> None:
    assert bool(detect_markers(text)) is expected


def test_design_measurement_holds_as_a_rate() -> None:
    positives = [text for text, gold in DESIGN_CASES if gold]
    negatives = [text for text, gold in DESIGN_CASES if not gold]
    recall = sum(bool(detect_markers(t)) for t in positives) / len(positives)
    false_positive = sum(bool(detect_markers(t)) for t in negatives) / len(negatives)
    assert (recall, false_positive) == (1.0, 0.0)


@pytest.mark.parametrize(
    "term",
    [
        "기밀",
        "영업비밀",
        "극비",
        "취급주의",
        "사외비",
        "부외비",
        "내부용",
        "秘",
        "極秘",
        "RESTRICTED",
        "PROPRIETARY",
        "TLP:RED",
        "TLP:AMBER",
        "INTERNAL USE ONLY",
    ],
)
def test_dictionary_terms_from_the_design_are_actually_wired(term: str) -> None:
    """22 of these were absent while the probe still passed."""
    assert term in _MARKER_TERMS
    assert detect_markers(f"{term}\n본문 내용입니다"), f"{term} is registered but never fires"


def test_numeric_classification_grades_are_recognized() -> None:
    assert detect_markers("2급비밀\n작전 계획")
    assert detect_markers("보안등급: 3급\n인사 자료")


def test_longer_terms_win_over_their_substrings() -> None:
    """`영업비밀` must not be truncated to `비밀`, nor `INTERNAL USE ONLY` to `INTERNAL ONLY`."""
    (finding,) = detect_markers("영업비밀\n제조 공정")
    assert finding.span == (0, 4)
    (english,) = detect_markers("INTERNAL USE ONLY\nQ3 plan")
    assert english.span == (0, len("INTERNAL USE ONLY"))


def test_threshold_is_policy_controlled_not_hardcoded() -> None:
    """§2.5 says the 10-sample threshold must be recalibrated on a real corpus."""
    # 첫 3줄(+2)뿐이고 단독도 등급표기도 아니라 점수가 정확히 2다. (`이 자료는 대외비` 같은
    # 표시 문장은 단독 표식처럼 센다 — 전수 검토 2026-10-28.)
    body_mention = "참고로 대외비 자료 목록을 정리했습니다"
    assert not detect_markers(body_mention, score_threshold=3)
    assert detect_markers(body_mention, score_threshold=2)


def test_marker_reaches_the_verdict_as_warn() -> None:
    """§2.5: 기밀 표식은 block 이 아니라 warn 이다."""
    result = Inspector("config/policy.yaml").check("대외비\n\n2026년 3분기 사업계획")
    assert result.verdict == "warn"
    assert [f.code for f in result.leaks] == ["L-DOC-01"]


def test_everyday_mentions_do_not_reach_a_verdict() -> None:
    for text, gold in DESIGN_CASES:
        if gold:
            continue
        assert Inspector("config/policy.yaml").check(text).verdict == "allow", text
