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
            "qwen3guard_stream": TeacherPrediction("INJECTION", 0.99),
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
            "qwen3guard_stream": TeacherPrediction("JAILBREAK", 0.9),
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
            "qwen3guard_stream": TeacherPrediction("JAILBREAK", 0.99),
        },
        policy,
    )
    assert decision.status == "review"
