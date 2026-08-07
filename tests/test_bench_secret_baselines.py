"""§7.3 시크릿 베이스라인 — 기성 도구를 우리 자극에 태운다."""

from __future__ import annotations

import pytest

from bench.secret_baselines import BaselineScore, evaluate, garim_secrets, run


def test_missing_tool_is_skipped_not_scored_zero() -> None:
    """★ "안 돌았다" 와 "못 잡았다" 는 다르다 — 0점을 주면 표가 거짓말을 한다."""
    skipped = BaselineScore(name="absent", available=False, note="not installed; skipped")
    payload = skipped.as_dict()
    assert payload["available"] is False
    assert "recall" not in payload
    assert "f1" not in payload


def test_evaluate_needs_both_arms() -> None:
    with pytest.raises(ValueError, match="both positives and negatives"):
        evaluate(["x"], [], lambda t: True, name="broken")


def test_garim_recall_and_false_positive_rate_on_a_tiny_slice() -> None:
    positives = ["설정에 sk-proj-AbCd1234efGH5678ijKL 를 넣었습니다"]
    negatives = ["설정에 YOUR_API_KEY 를 넣었습니다"]
    score = evaluate(positives, negatives, garim_secrets(), name="garim-l0")
    assert score.recall == 1.0
    assert score.false_positive_rate == 0.0
    assert score.f1 == 1.0


def test_run_returns_a_row_per_candidate() -> None:
    positives = ["토큰은 ghp_AbCd1234efGH5678ijKLmnOPqrST12345 입니다"]
    negatives = ["토큰은 <your-token-here> 입니다"]
    scores = {score.name: score for score in run(positives, negatives)}
    assert set(scores) == {"garim-l0", "detect-secrets", "credsweeper"}
    assert scores["garim-l0"].available
