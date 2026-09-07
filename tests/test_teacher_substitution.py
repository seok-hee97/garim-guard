"""Stream → Gen 교사 교체. 왜 바꿨고, 무엇을 지켜야 하는가."""

from __future__ import annotations

import json

import pytest

from bench.teacher_agreement import (
    RUBBER_STAMP_THRESHOLD,
    generator_agreement,
    jailbreak_independence,
    main,
)
from garim.training.teacher_io import TeacherArtifact
from garim.training.teachers import load_teacher_policy


def _artifact(identifier: str, label: str) -> TeacherArtifact:
    return TeacherArtifact(
        id=identifier,
        teacher="probe",
        model_id="probe/model",
        revision="0" * 40,
        label=label,  # type: ignore[arg-type]
        confidence=0.9,
        class_probabilities={label: 0.9},
    )


def test_jailbreak_still_has_two_voters() -> None:
    """★ Qwen 을 빼면 4개 클래스 중 하나가 소멸한다.

    `minimum_votes: 2` 인데 JAILBREAK 에 투표하는 교사가 gemma4 와 Qwen 둘뿐이다. 빼면
    정족수를 영영 못 채우고 `--drop-review` 에서 전부 폐기된다. 교체가 필요했던 이유이자,
    교체 후에도 지켜져야 하는 성질이다.
    """
    policy = load_teacher_policy("train/teachers.yaml")
    voters = [name for name, spec in policy.teachers.items() if "JAILBREAK" in spec.votes_for]
    assert len(voters) >= policy.minimum_votes, (voters, policy.minimum_votes)


def test_the_substituted_teacher_keeps_its_guardrails() -> None:
    """§6.2.2 가 Gen-0.6B 의 폴란드 FPR 17.17% 를 명시했다 — 방어 장치가 남아야 한다."""
    policy = load_teacher_policy("train/teachers.yaml")
    spec = policy.teachers["qwen3guard_gen"]
    assert spec.votes_for == ["JAILBREAK"], "범용 Safe/Unsafe 는 투표가 아니다"
    assert spec.can_flip_none is False, "Gemma-NONE 을 공격으로 뒤집지 못한다"
    assert policy.minimum_votes >= 2, "Gemma 동의 없이 JAILBREAK 이 들어오면 안 된다"


def test_no_stale_reference_to_the_unusable_stream_model() -> None:
    """정책과 CLI 가 갈리면 한쪽만 고치게 된다 — 실제로 `invalid choice` 로 막혔다."""
    policy = load_teacher_policy("train/teachers.yaml")
    assert "qwen3guard_stream" not in policy.teachers
    from garim.training.teacher_inference import load_teacher_backend

    with pytest.raises(ValueError, match="no reviewed inference adapter"):
        load_teacher_backend("qwen3guard_stream_typo", policy.teachers["kanana"], ".")


def test_generator_agreement_counts_only_eligible_shared_ids() -> None:
    artifacts = {"a": _artifact("a", "INJECTION"), "b": _artifact("b", "NONE")}
    report = generator_agreement(
        artifacts,
        {"a": "INJECTION", "b": "JAILBREAK", "c": "NONE"},
        eligible_labels={"INJECTION"},
    )
    assert report["rows"] == 1
    assert report["agreement"] == 1.0
    assert "JAILBREAK->NONE" not in report["confusion"]


def test_generator_agreement_refuses_a_disjoint_pair() -> None:
    with pytest.raises(ValueError, match="no eligible overlapping ids"):
        generator_agreement(
            {"a": _artifact("a", "NONE")},
            {"z": "NONE"},
            eligible_labels={"NONE"},
        )


def test_rubber_stamp_is_flagged() -> None:
    """★ 과탐이 심한 교사가 2표 중 1표를 맡으면 사실상 Gemma 단독 판정이 된다.

    설계가 세 번째 교사를 넣은 이유가 그 상태를 막으려는 것이므로, 동의율이 임계를
    넘으면 교체가 실패한 것으로 본다.
    """
    primary = {str(index): _artifact(str(index), "JAILBREAK") for index in range(20)}
    always = {key: _artifact(key, "JAILBREAK") for key in primary}
    assert jailbreak_independence(primary, always)["is_rubber_stamp"]

    mixed = {
        key: _artifact(key, "JAILBREAK" if int(key) % 2 else "NONE") for key in primary
    }
    report = jailbreak_independence(primary, mixed)
    assert not report["is_rubber_stamp"]
    assert report["agreement_on_jailbreak"] == 0.5
    assert report["rubber_stamp_threshold"] == RUBBER_STAMP_THRESHOLD


def test_independence_is_none_when_the_primary_never_says_jailbreak() -> None:
    """kanana 는 JAILBREAK 클래스가 없다 — primary 로 쓰면 잴 것이 없다."""
    primary = {"a": _artifact("a", "INJECTION")}
    report = jailbreak_independence(primary, {"a": _artifact("a", "JAILBREAK")})
    assert report["agreement_on_jailbreak"] is None
    assert not report["is_rubber_stamp"]


def test_cli_returns_nonzero_when_the_substitute_is_a_rubber_stamp(tmp_path) -> None:
    gold = {str(index): "JAILBREAK" for index in range(20)}
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    teacher_paths = {}
    policy = load_teacher_policy("train/teachers.yaml")
    for name in ("gemma4", "qwen3guard_gen"):
        spec = policy.teachers[name]
        path = tmp_path / f"{name}.jsonl"
        path.write_text(
            "\n".join(
                _artifact(identifier, "JAILBREAK")
                .model_copy(
                    update={
                        "teacher": name,
                        "model_id": spec.model_id,
                        "revision": spec.revision,
                    }
                )
                .model_dump_json()
                for identifier in gold
            )
            + "\n",
            encoding="utf-8",
        )
        teacher_paths[name] = path
    status = main(
        [
            "--gold",
            str(gold_path),
            "--teacher",
            f"gemma4={teacher_paths['gemma4']}",
            "--teacher",
            f"qwen3guard_gen={teacher_paths['qwen3guard_gen']}",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )
    assert status == 2


def test_gold_sidecar_is_not_part_of_the_teacher_input() -> None:
    """정답을 교사에게 보여 주면 일치도가 아니라 정답 복사를 재게 된다."""
    from garim.training.data import load_jsonl

    rows = load_jsonl("data/corpus/attack-unlabeled.jsonl")
    assert rows and all(record.attack_label is None for record in rows)
    gold = json.loads(open("data/corpus/attack-generator-gold.json", encoding="utf-8").read())
    assert set(gold) == {record.id for record in rows}
