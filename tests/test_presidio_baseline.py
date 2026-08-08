"""§7.3 Presidio 베이스라인. 허수아비를 만들지 않았는지가 이 파일의 주제다."""

from __future__ import annotations

import pytest

presidio = pytest.importorskip("presidio_analyzer", reason="presidio-analyzer 미설치")

from bench.presidio_baseline import (  # noqa: E402
    _ENTITY_TO_LABEL,
    INVALID_COMPARISON_LABELS,
    STRICT_THRESHOLD,
    presidio_ko_detector,
    presidio_vanilla_detector,
)


def test_all_five_korean_recognizers_are_actually_loaded() -> None:
    """§7.3: "반드시 활성 상태로 돌린다 — 끄고 비교하면 0점짜리 허수아비가 된다"."""
    from presidio_analyzer import predefined_recognizers

    for name in (
        "KrRrnRecognizer",
        "KrFrnRecognizer",
        "KrBrnRecognizer",
        "KrPassportRecognizer",
        "KrDriverLicenseRecognizer",
    ):
        assert hasattr(predefined_recognizers, name), f"{name} 이 없다 — 비교가 성립하지 않는다"
    for entity in ("KR_RRN", "KR_FRN", "KR_BRN", "KR_PASSPORT", "KR_DRIVER_LICENSE"):
        assert entity in _ENTITY_TO_LABEL


def test_korean_arm_finds_what_the_vanilla_arm_cannot() -> None:
    """두 팔이 같은 답을 내면 팔을 나눈 의미가 없다."""
    text = "주민등록번호는 900101-1234567 입니다"
    assert presidio_ko_detector()(text)
    assert not presidio_vanilla_detector()(text)


def test_phone_recognizer_is_configured_for_korea() -> None:
    """기본 지역(US)으로 한국 번호를 재면 Presidio 가 아니라 오설정을 재게 된다."""
    assert presidio_ko_detector()("연락처 010-1234-5678 입니다")


def test_email_is_declared_an_invalid_comparison_not_a_win() -> None:
    """★ 우리 코퍼스가 예약 TLD 를 써서 Presidio 가 못 잡는다 — 우리 승리가 아니다.

    `.test` 대신 실제 TLD 를 주면 Presidio 는 신뢰도 1.0 으로 정확히 잡는다. 그걸
    0.000 으로 적고 macro 에 넣으면 §7.3 이 경고한 바로 그 허수아비가 된다.
    """
    assert "EMAIL" in INVALID_COMPARISON_LABELS
    detector = presidio_ko_detector()
    assert not detector("메일은 test1@example.test 입니다")
    assert detector("메일은 test1@example.com 입니다")


def test_strict_arm_is_a_subset_of_the_default_arm() -> None:
    """임계를 올리는 것은 결과를 걸러낼 뿐 새 결과를 만들지 않는다."""
    text = "사업자번호 123-45-67890, 주민번호 900101-1234567, 여권 M12345678"
    default = set(presidio_ko_detector()(text))
    strict = set(presidio_ko_detector(score_threshold=STRICT_THRESHOLD)(text))
    assert strict <= default
