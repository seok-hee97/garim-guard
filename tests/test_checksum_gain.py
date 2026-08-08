"""§7.2 S1 — 사전 등록된 예측을 실측과 맞대는 하네스. 회귀 기준선이기도 하다."""

from __future__ import annotations

from bench.checksum_gain import SOURCES, ablated, measure
from garim.detect.rules import checksum, kr_pii


def test_ablation_restores_every_patched_predicate() -> None:
    """절개가 새면 이 파일 이후의 모든 테스트가 조용히 거짓이 된다."""
    before = (
        kr_pii.rrn_date_sex_valid,
        kr_pii.rrn_checksum_valid,
        checksum.rrn_date_sex_valid,
    )
    with ablated("checksum"):
        assert kr_pii.rrn_date_sex_valid("무엇이든") is True
    assert (
        kr_pii.rrn_date_sex_valid,
        kr_pii.rrn_checksum_valid,
        checksum.rrn_date_sex_valid,
    ) == before


def test_checksum_arm_does_not_smuggle_the_date_check_back_in() -> None:
    """`rrn_checksum_valid` 가 제 안에서 날짜를 다시 본다(checksum.py:27).

    그쪽을 같이 끄지 않으면 "체크섬만" 팔에 3단계가 몰래 남아 기여도가 잘못 갈린다.
    """
    with ablated("checksum"):
        assert checksum.rrn_date_sex_valid("아무거나") is True


def test_arms_are_monotone() -> None:
    """검증을 더 켜면 오탐은 늘 수 없다. 늘었다면 절개가 잘못된 것이다."""
    for index, source in enumerate(SOURCES):
        rates = measure(source, count=400, seed=index)
        assert rates["regex_only"] >= rates["checksum"] >= rates["full"], (source.key, rates)


def test_the_date_stage_is_calendar_validity_not_plausibility() -> None:
    """★ 예측이 빗나간 자리를 고정한다.

    §2.1 은 주문번호에서 3단계가 8.8% → 0.34% 를 만든다고 예측했다. 실측은
    4.3% → 4.3% 로 **아무것도 안 한다.** 이유는 여기 있다 — 주문일 `240315` 를 주민번호
    앞머리로 읽으면 1924년생이거나 2024년생이고 **둘 다 달력상 실재하는 날짜**다.
    출하 코드의 3단계는 달력 유효성만 보고 나이의 그럴듯함은 안 본다.

    이 테스트는 그 성질을 **고정할 뿐 옳다고 말하지 않는다.** 나이 상한을 넣으면
    오탐은 줄지만 고령자의 진짜 주민번호를 놓치고, `date.today()` 가 들어와 판정이
    시간에 따라 달라진다. 바꾸려면 그 대가를 알고 바꿔야 한다.
    """
    for gender in "12345678":
        assert checksum.rrn_date_sex_valid(f"240315{gender}000000")


def test_prefix_shape_decides_whether_the_date_stage_helps() -> None:
    """날짜 앞머리면 3단계가 놀고, 순수 일련번호면 크게 건진다 — 하나의 배수가 없다."""
    order = measure(SOURCES[0], count=4_000, seed=1)
    invoice = measure(SOURCES[2], count=4_000, seed=3)
    assert order["checksum"] == order["full"], "주문번호에서 3단계는 기여가 없어야 한다"
    assert invoice["full"] < invoice["checksum"] / 5, "송장번호에서는 크게 줄어야 한다"
