"""§8.1-13b 마스킹 응답 품질 하네스. 클라이언트를 주입하므로 API 없이 돈다."""

from __future__ import annotations

import re

import pytest

from bench.mask_quality import (
    CONTENT,
    IDENTITY,
    INDEPENDENT,
    Client,
    assert_synthetic_only,
    build_trials,
    masked_label_coverage,
    run,
)


def test_trials_cover_all_three_families() -> None:
    """한 갈래만으로는 못 잰다 — 첫 판이 분류만 넣었다가 양쪽 만점으로 천장에 붙었다."""
    trials = build_trials(24)
    assert {t.family for t in trials} == {INDEPENDENT, IDENTITY, CONTENT}


def test_every_trial_actually_gets_masked() -> None:
    """마스킹이 안 걸리면 대조가 공허하다."""
    trials = build_trials(24)
    assert all(t.original_prompt != t.masked_prompt for t in trials)
    assert masked_label_coverage(trials) == {"PHONE", "EMAIL"}


def test_masking_preserves_value_identity() -> None:
    """★ `identity` 갈래가 시험하는 것은 모델이 아니라 우리 마스커다.

    같은 값은 같은 번호로, 다른 값은 다른 번호로 가려져야 한다. 이 성질이 깨지면
    "두 연락처가 동일한가" 같은 정상 업무가 마스킹 때문에 틀린 답을 받는다.

    번호는 `Vault` 가 값별로 매기고 `Inspector` 수명 내내 유지된다(`_coreference_key`) —
    같은 사람이 여러 턴에 걸쳐 같은 자리표시자를 받는다. 그래서 **특정 번호를 못 박으면
    안 되고**, 같음/다름이라는 관계만 검사한다.
    """
    trials = build_trials(12)
    same = next(t for t in trials if t.family == IDENTITY and t.gold == "동일")
    different = next(t for t in trials if t.family == IDENTITY and t.gold == "상이")
    assert len(set(re.findall(r"<PHONE_\d+>", same.masked_prompt))) == 1
    assert same.masked_prompt.count("<PHONE_") == 2
    assert len(set(re.findall(r"<PHONE_\d+>", different.masked_prompt))) == 2


def test_content_family_gold_is_gone_from_the_masked_arm() -> None:
    """양성 대조다 — 마스킹본이 정말 정보를 잃었어야 나머지 0.000 이 의미를 갖는다."""
    for trial in build_trials(24):
        if trial.family == CONTENT:
            assert trial.gold not in trial.masked_prompt
            assert trial.gold in trial.original_prompt


def _oracle(trials: list) -> Client:
    """프롬프트에 답의 근거가 남아 있으면 맞히는 완벽한 모델."""
    lookup = {t.original_prompt: t.gold for t in trials}
    lookup |= {t.masked_prompt: t.gold for t in trials}

    reasoned = {"환불", "배송", "긍정", "부정", "동일", "상이"}

    def client(prompt: str) -> str:
        gold = lookup.get(prompt, "")
        if gold and (gold in prompt or gold in reasoned):
            return gold
        return "모름"

    return client


def test_headline_excludes_intended_loss_but_reports_it() -> None:
    """가림이 잘 가릴수록 점수가 나빠지는 지표를 만들면 안 된다 — 빼되 숨기지 않는다."""
    trials = build_trials(24)
    report = run(trials, _oracle(trials), model="oracle")
    payload = report.as_dict()
    assert payload["headline"]["trials"] == 16
    assert payload["headline_excludes"] == [CONTENT]
    assert payload["intended_loss"]["trials"] == 8
    assert payload["intended_loss"]["relative_degradation"] == 1.0
    assert payload["within_target"]


def test_degradation_is_none_when_the_original_arm_scores_zero() -> None:
    """원문이 0 이면 비율이 성립하지 않는다 — 저하 0.000(통과)로 보고하면 거짓이다."""
    trials = build_trials(12)
    report = run(trials, lambda _: "전혀 상관없는 답", model="null")
    assert report.as_dict()["headline"]["relative_degradation"] is None
    assert not report.within_target


def test_synthetic_guard_rejects_values_outside_the_generator() -> None:
    """§7.4 — 실제 개인정보가 API 로 나가는 경로를 만들지 않는다."""
    assert_synthetic_only("연락처는 010-1234-5678, 메일은 x@example.test")
    with pytest.raises(ValueError, match="전화번호"):
        assert_synthetic_only("연락처는 010-9999-8888 입니다")
    with pytest.raises(ValueError, match="이메일"):
        assert_synthetic_only("메일은 someone@gmail.com 입니다")


def test_run_refuses_an_empty_trial_set() -> None:
    with pytest.raises(ValueError, match="at least one"):
        run([], lambda _: "", model="none")
