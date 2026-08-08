"""§5.3.2 표기 변형 축. 이 축이 비어 있으면 L1 이 배울 것이 없다."""

from __future__ import annotations

import json
import re

import pytest

from garim.training.notation import (
    LINEBREAK,
    NO_SEPARATOR,
    NOTATION_WEIGHTS,
    SPACED,
    SPOKEN,
    STANDARD,
    apply_notation,
    notation_distribution,
    planned_notation,
)
from garim.training.synthetic import generate_pii_corpus


@pytest.fixture(scope="module")
def corpus() -> list:
    return generate_pii_corpus()


def _spans_with_notation(records: list) -> list[tuple[str, str, str]]:
    """(라벨, 기록된 표기, 스팬 문자열) — 기록된 것으로만 센다.

    문자열에서 표기를 되짚으면 프로브가 알고리즘을 다시 구현하는 셈이고, 그게 사전
    22칸이 빈 것을 못 잡은 `marker_probe` 의 실패였다.
    """
    rows: list[tuple[str, str, str]] = []
    for record in records:
        applied = record.meta.get("notations") or (
            [record.meta["notation"]] if "notation" in record.meta else []
        )
        for span, notation in zip(record.spans, applied, strict=False):
            rows.append((span.label, notation, record.text[span.start : span.end]))
    return rows


def test_every_notation_appears_in_the_corpus(corpus: list) -> None:
    """★ 이 테스트가 잡는 회귀가 §5.3.2 미구현이었다.

    이전에는 식별자 양성 22,500행이 **전부 표준 구분자**였다. PHONE 양성 2,000건이
    하나도 빠짐없이 `010-1234-5678` 꼴이라 L0 정규식이 100% 잡았고, §3.2.3 이 L1 의
    존재 근거로 적은 *"L1이 L0가 놓치는 변형을 학습해야 앙상블이 성립한다"* 가
    데이터로 뒷받침되지 않았다.
    """
    distribution = notation_distribution(corpus)
    for notation, _weight in NOTATION_WEIGHTS:
        assert distribution[notation] > 500, (notation, distribution)


def test_variant_ratios_match_the_design_weights(corpus: list) -> None:
    """`standard` 는 되돌아간 것을 흡수하므로 **변형 넷의 서로간 비율**로 검증한다."""
    counts = {notation: 0 for notation, _ in NOTATION_WEIGHTS}
    for _label, notation, _text in _spans_with_notation(corpus):
        counts[notation] += 1
    planned = dict(NOTATION_WEIGHTS)
    base = counts[SPACED] / planned[SPACED]
    for notation in (NO_SEPARATOR, LINEBREAK, SPOKEN):
        assert counts[notation] / planned[notation] == pytest.approx(base, rel=0.03), (
            notation,
            counts,
        )


def test_recorded_notation_matches_the_span_it_describes(corpus: list) -> None:
    """계획 표기를 적으면 코퍼스가 거짓을 말한다 — 적용된 것만 적어야 한다."""
    for label, notation, text in _spans_with_notation(corpus):
        if notation == SPOKEN:
            assert not re.search(r"\d", text), (label, text)
            assert re.search(r"[공일이삼사오육칠팔구]", text), (label, text)
        elif notation == LINEBREAK:
            assert "\n" in text, (label, text)
        elif notation == NO_SEPARATOR:
            assert "-" not in text and " " not in text, (label, text)
        elif notation == SPACED:
            assert " " in text and "-" not in text, (label, text)
        else:
            assert notation == STANDARD and "\n" not in text, (label, text)


def test_spans_still_point_at_the_written_value(corpus: list) -> None:
    """변형을 값에 걸고 `_compose` 가 그 길이로 스팬을 잡으므로 저절로 맞아야 한다."""
    for label, _notation, text in _spans_with_notation(corpus):
        assert text.strip() == text, (label, repr(text))
        assert text, label


def test_linebreak_never_lands_inside_a_wrapper_that_it_would_break(corpus: list) -> None:
    """★ `structured` 는 `{"payload": "…"}` 다 — 생 개행이 들어가면 JSON 이 깨진다.

    깨진 JSON 은 조용히 다른 것을 재게 만든다: `content_type` 감지가 `structured` 를
    못 알아보고 `prose` 정책이 적용되면 S4 대조가 무의미해진다.
    """
    structured = [
        record
        for record in corpus
        if record.meta["doc_shape"] == "structured" and record.spans
    ]
    assert structured, "structured 행이 없으면 이 테스트가 아무것도 안 지킨다"
    for record in structured:
        assert record.meta.get("notation") != LINEBREAK
        json.loads(record.text)  # 깨지면 여기서 터진다


def test_paired_values_are_left_standard(corpus: list) -> None:
    """짝 음성이 같은 값을 재사용하므로 양성 쪽만 바꾸면 쌍이 깨진다(§5.3.2)."""
    paired = [record for record in corpus if record.pair_id and record.spans]
    assert paired
    assert all(record.meta["notation"] == STANDARD for record in paired)
    values = {record.pair_id: record.text for record in paired}
    negatives = {
        record.pair_id: record.text
        for record in corpus
        if record.pair_id and not record.spans
    }
    shared = set(values) & set(negatives)
    assert shared, "쌍이 하나도 안 맞으면 대조가 성립하지 않는다"
    for pair_id in sorted(shared)[:50]:
        positive = next(
            record for record in paired if record.pair_id == pair_id
        )
        value = positive.text[positive.spans[0].start : positive.spans[0].end]
        assert value in negatives[pair_id], (pair_id, value)


@pytest.mark.parametrize(
    ("value", "notation", "shape", "expected"),
    [
        ("900101-1234567", SPACED, "prose", "900101 1234567"),
        ("900101-1234567", NO_SEPARATOR, "prose", "9001011234567"),
        ("900101-1234567", LINEBREAK, "prose", "900101-\n1234567"),
        ("010-1234-5678", SPOKEN, "prose", "공일공 일이삼사 오육칠팔"),
    ],
)
def test_transforms_produce_the_forms_the_design_names(
    value: str, notation: str, shape: str, expected: str
) -> None:
    assert apply_notation(value, notation, doc_shape=shape) == (expected, notation)


@pytest.mark.parametrize(
    ("value", "notation", "shape", "why"),
    [
        ("M12345678", SPOKEN, "prose", "글자가 섞인 값은 소리로 읽을 수 없다"),
        ("M12345678", SPACED, "prose", "구분자가 없어 바꿀 것이 없다"),
        ("4242 4242 4242 4242", SPACED, "prose", "이미 공백으로 끊는 값이다"),
        ("900101-1234567", LINEBREAK, "structured", "JSON 래퍼 안에 생 개행을 넣을 수 없다"),
    ],
)
def test_inapplicable_notation_falls_back_and_says_so(
    value: str, notation: str, shape: str, why: str
) -> None:
    written, applied = apply_notation(value, notation, doc_shape=shape)
    assert applied == STANDARD, why
    assert written == value


def test_schedule_is_deterministic_and_spreads_the_variants() -> None:
    """가중치 순서 그대로 쓰면 앞 50칸이 전부 `standard` 라 표기가 행 순서와 붙는다."""
    first_hundred = [planned_notation(index) for index in range(100)]
    assert first_hundred == [planned_notation(index + 100) for index in range(100)]
    assert len(set(first_hundred[:20])) > 1
