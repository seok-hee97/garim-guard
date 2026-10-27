from __future__ import annotations

from garim.training.teachers import TeacherPrediction, load_teacher_policy, resolve_teacher_label


def test_teacher_policy_has_two_eligible_voters_per_student_class() -> None:
    policy = load_teacher_policy()
    for label in policy.classes:
        assert sum(label in teacher.votes_for for teacher in policy.teachers.values()) >= 2


def test_injection_cannot_receive_a_qwen_generic_unsafe_vote() -> None:
    policy = load_teacher_policy()
    decision = resolve_teacher_label(
        {
            "kanana": TeacherPrediction("INJECTION", 0.9),
            "gemma4": TeacherPrediction("NONE", 0.8),
            "qwen3guard_gen": TeacherPrediction("INJECTION", 0.99),
        },
        policy,
    )
    assert decision.status == "review"
    assert decision.votes["INJECTION"] == 1


def test_jailbreak_requires_gemma_and_qwen_agreement() -> None:
    policy = load_teacher_policy()
    decision = resolve_teacher_label(
        {
            "gemma4": TeacherPrediction("JAILBREAK", 0.8),
            "qwen3guard_gen": TeacherPrediction("JAILBREAK", 0.9),
        },
        policy,
    )
    assert decision.status == "accepted"
    assert decision.label == "JAILBREAK"


def test_low_confidence_vote_is_sent_to_review() -> None:
    policy = load_teacher_policy()
    decision = resolve_teacher_label(
        {
            "gemma4": TeacherPrediction("JAILBREAK", 0.49),
            "qwen3guard_gen": TeacherPrediction("JAILBREAK", 0.99),
        },
        policy,
    )
    assert decision.status == "review"


def _three(kanana: str, gemma: str, qwen: str = "NONE") -> dict[str, TeacherPrediction]:
    return {
        "kanana": TeacherPrediction(kanana, 0.9),
        "gemma4": TeacherPrediction(gemma, 0.9),
        "qwen3guard_gen": TeacherPrediction(qwen, 0.9),
    }


def test_generator_benign_survives_a_single_teacher_false_alarm() -> None:
    """R1 이 폐기한 두 조합 — (kanana NONE, Gemma SYSTEM_LEAK) · (kanana INJECTION, Gemma NONE)."""
    policy = load_teacher_policy()
    for kanana, gemma in (("NONE", "SYSTEM_LEAK"), ("INJECTION", "NONE")):
        decision = resolve_teacher_label(_three(kanana, gemma), policy, generator_label="NONE")
        assert (decision.status, decision.label) == ("accepted", "NONE")
        # 생성기 표가 없으면 R1 처럼 폐기된다.
        assert resolve_teacher_label(_three(kanana, gemma), policy).status == "review"


def test_generator_benign_is_never_accepted_as_an_attack() -> None:
    policy = load_teacher_policy()
    decision = resolve_teacher_label(
        _three("INJECTION", "INJECTION"), policy, generator_label="NONE"
    )
    assert decision.status == "review"
    assert decision.reason == "generator_conflict"
    split = resolve_teacher_label(
        _three("INJECTION", "SYSTEM_LEAK"), policy, generator_label="NONE"
    )
    assert split.status == "review"


def test_generator_attack_needs_teacher_agreement_on_the_same_label() -> None:
    policy = load_teacher_policy()
    agreed = resolve_teacher_label(
        _three("INJECTION", "INJECTION"), policy, generator_label="INJECTION"
    )
    assert (agreed.status, agreed.label) == ("accepted", "INJECTION")
    # 생성기는 공격에 투표하지 않는다 — 교사 하나만 맞으면 폐기다.
    lone = resolve_teacher_label(_three("INJECTION", "NONE"), policy, generator_label="INJECTION")
    assert lone.status == "review"
    # 은닉 때문에 두 교사가 다 NONE 이라 한 자작 공격 — R1 은 NONE 으로 학습했다.
    hidden = resolve_teacher_label(_three("NONE", "NONE"), policy, generator_label="INJECTION")
    assert (hidden.status, hidden.reason) == ("review", "generator_conflict")
