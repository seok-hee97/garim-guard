"""§5.4 T5 — 생성기를 안 거친 양성. §7.1.0 이 H1 재현율의 유일한 근거라 적었다."""

from __future__ import annotations

from bench.leak_eval import evaluate, garim_leak_detector
from garim.training.data import validate_corpus
from garim.training.synthetic import generate_pii_corpus
from garim.training.t5_positives import UNFILLED_SLICES, generate_t5


def test_t5_never_touches_the_generator() -> None:
    rows = generate_t5()
    assert rows
    assert all(row.meta["generator_free"] for row in rows)
    assert all(row.source == "garim-t5" for row in rows)
    # 평가 전용이다 — 학습에 들어가면 §7.1.0 이 막으려던 항등식이 돌아온다.
    assert {row.split for row in rows} == {"test"}


def test_spans_point_at_the_value() -> None:
    for row in generate_t5():
        (span,) = row.spans
        assert row.text[span.start : span.end].strip()
        assert span.end <= len(row.text)


def test_t5_satisfies_the_corpus_contract() -> None:
    validate_corpus(generate_t5())


def test_t5_is_deterministic() -> None:
    assert [r.text for r in generate_t5()] == [r.text for r in generate_t5()]


def test_generator_free_recall_is_lower_than_synthetic() -> None:
    """★ §7.1.0 의 항등식 경고가 수치로 확인된다.

    합성 양성은 생성기가 만들고 같은 규칙이 잡으므로 재현율이 부풀려진다.

    ⚠️ macro F1 끼리 비교하면 안 된다 — 두 셋의 라벨 구성이 다르다(그렇게 쟀다가
    T5 가 더 높게 나왔다). **양쪽에 있는 라벨의 재현율**을 비교해야 같은 것을 잰다.
    """
    detector = garim_leak_detector()
    synthetic = [r for r in generate_pii_corpus() if r.split == "test" and r.spans][:800]
    on_synthetic = evaluate(synthetic, detector, name="synthetic")
    on_t5 = evaluate(generate_t5(), detector, name="t5")

    shared = set(on_synthetic.by_label) & set(on_t5.by_label)
    assert shared, "두 셋에 공통 라벨이 없으면 비교 자체가 성립하지 않는다"
    dropped = [
        label
        for label in shared
        if on_t5.by_label[label].recall < on_synthetic.by_label[label].recall
    ]
    assert dropped, "생성기 밖 양성에서 아무 라벨도 안 떨어지면 T5 가 제 역할을 못 한다"
    # PHONE 이 대표적이다 — 합성 1.000, 자연어 표기가 섞인 T5 는 0.4 대.
    assert on_t5.by_label["PHONE"].recall < on_synthetic.by_label["PHONE"].recall


def test_spelled_out_phone_is_the_known_gap() -> None:
    """§7.7 F4 — 자연어로 풀어 쓴 식별자는 "부분" 이라고 미리 적어둔 실패 경로다."""
    report = evaluate(generate_t5(), garim_leak_detector(), name="t5")
    assert report.by_label["PHONE"].recall < 0.6


def test_unfilled_slices_are_declared_not_faked() -> None:
    """사람이 문장을 써야 하는 칸은 규모만 남긴다 — 채운 척하지 않는다."""
    assert sum(UNFILLED_SLICES.values()) > 0
    assert "third_generation_rrn" in UNFILLED_SLICES
    assert "contextual_person_address" in UNFILLED_SLICES
