"""Auditable teacher prediction artifacts and accepted/review corpus resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from garim.training.data import CorpusRecord, validate_corpus
from garim.training.split import component_validation_splits
from garim.training.teachers import (
    ATTACK_LABELS,
    JAILBREAK_POSTERIOR_CLASSES,
    SOFT_TARGET_CLASSES,
    AttackLabel,
    TeacherPolicy,
    TeacherPrediction,
    TeacherSpec,
    resolve_teacher_label,
)


class TeacherArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    teacher: str
    model_id: str
    revision: str
    label: AttackLabel
    confidence: float = Field(ge=0.0, le=1.0)
    class_probabilities: dict[str, float] | None = None
    prompt_version: str | None = None

    @model_validator(mode="after")
    def validate_probabilities(self) -> TeacherArtifact:
        if self.class_probabilities is not None:
            if any(not 0.0 <= value <= 1.0 for value in self.class_probabilities.values()):
                raise ValueError("class probabilities must be in [0, 1]")
            if not self.class_probabilities:
                raise ValueError("class probabilities cannot be empty")
        return self


def load_teacher_artifacts(path: str | Path) -> dict[str, TeacherArtifact]:
    source = Path(path)
    output: dict[str, TeacherArtifact] = {}
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                artifact = TeacherArtifact.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: {exc}") from exc
            if artifact.id in output:
                raise ValueError(f"{source}: duplicate prediction id {artifact.id}")
            output[artifact.id] = artifact
    return output


def _validate_soft_targets(
    record_id: str, teacher: str, spec: TeacherSpec, artifact: TeacherArtifact
) -> None:
    """Hold every teacher to the soft-target contract its own manifest entry declares."""
    if not spec.soft_targets:
        return
    declared = set(spec.soft_targets)
    if artifact.class_probabilities is None or set(artifact.class_probabilities) != declared:
        raise ValueError(
            f"{record_id}: {teacher} must emit exactly {sorted(declared)} soft-target classes"
        )
    # A single-class head is a posterior, not a distribution — only ask a multi-class
    # soft target to sum to one.
    if len(declared) > 1 and abs(sum(artifact.class_probabilities.values()) - 1.0) > 1e-4:
        raise ValueError(f"{record_id}: {teacher} soft targets must sum to one")


def resolve_teacher_corpus(
    records: Iterable[CorpusRecord],
    artifacts: dict[str, dict[str, TeacherArtifact]],
    policy: TeacherPolicy,
    *,
    validation_percent: int = 10,
    generator_labels: Mapping[str, AttackLabel] | None = None,
) -> tuple[list[CorpusRecord], list[CorpusRecord], list[dict[str, object]]]:
    """`generator_labels` 는 생성기 사이드카(`attack-generator-gold-*.json`)다 — 그 행만 C3 규칙."""
    if not 0 <= validation_percent < 100:
        raise ValueError("validation_percent must be in [0, 100)")
    accepted: list[CorpusRecord] = []
    review: list[CorpusRecord] = []
    audit: list[dict[str, object]] = []
    expected_teachers = set(policy.teachers)
    soft_target_teacher = policy.soft_target_teacher(SOFT_TARGET_CLASSES)
    jailbreak_teacher = policy.soft_target_teacher(JAILBREAK_POSTERIOR_CLASSES)
    materialized = list(records)
    record_ids = {record.id for record in materialized}
    if len(record_ids) != len(materialized):
        raise ValueError("teacher pool contains duplicate record IDs")
    unexpected_teachers = set(artifacts) - expected_teachers
    if unexpected_teachers:
        raise ValueError(f"unregistered teacher artifact groups: {unexpected_teachers}")
    for teacher, predictions in artifacts.items():
        extra = set(predictions) - record_ids
        if extra:
            raise ValueError(
                f"{teacher}: {len(extra)} prediction IDs are not in the teacher pool"
            )

    generator_labels = generator_labels or {}
    for record in materialized:
        if record.task != "attack" or record.attack_label is not None:
            raise ValueError(f"{record.id}: teacher pool must contain unlabeled attack rows")
        row_artifacts: dict[str, TeacherArtifact] = {}
        missing: list[str] = []
        for teacher in expected_teachers:
            artifact = artifacts.get(teacher, {}).get(record.id)
            if artifact is None:
                missing.append(teacher)
                continue
            spec = policy.teachers[teacher]
            if artifact.teacher != teacher:
                raise ValueError(f"{record.id}: artifact teacher name mismatch")
            if artifact.model_id != spec.model_id or artifact.revision != spec.revision:
                raise ValueError(f"{record.id}: {teacher} model/revision drift")
            _validate_soft_targets(record.id, teacher, spec, artifact)
            row_artifacts[teacher] = artifact

        generator_label = generator_labels.get(record.id)
        if missing:
            decision_label = None
            status = "review"
            votes = {label: 0 for label in ATTACK_LABELS}
            reason: str | None = "missing_teacher"
        else:
            decision = resolve_teacher_label(
                {
                    name: TeacherPrediction(item.label, item.confidence)
                    for name, item in row_artifacts.items()
                },
                policy,
                generator_label=generator_label,
            )
            decision_label, status, votes = decision.label, decision.status, decision.votes
            reason = decision.reason

        audit.append(
            {
                "id": record.id,
                "status": status,
                "label": decision_label,
                "votes": votes,
                "reason": reason,
                "generator_label": generator_label,
                "missing_teachers": missing,
                "predictions": {
                    name: {
                        "label": item.label,
                        "confidence": item.confidence,
                        "prompt_version": item.prompt_version,
                    }
                    for name, item in sorted(row_artifacts.items())
                },
            }
        )
        if decision_label is None:
            review.append(record)
            continue

        soft_probabilities = None
        if soft_target_teacher is not None:
            probabilities = row_artifacts[soft_target_teacher].class_probabilities
            if probabilities is not None:
                soft_probabilities = tuple(
                    probabilities[label] for label in SOFT_TARGET_CLASSES
                )
        jailbreak_target = None
        if jailbreak_teacher is not None:
            probabilities = row_artifacts[jailbreak_teacher].class_probabilities
            if probabilities is not None:
                jailbreak_target = probabilities[JAILBREAK_POSTERIOR_CLASSES[0]]
        silenced: list[str] = []
        if generator_label is not None:
            # ★ C3 은 하드 라벨만 바꾼다. 생성기 표로 NONE 이 된 행에 kanana 의 INJECTION 분포를
            #   그대로 붙이면 증류 항(가중 0.5)이 그 하드 네거티브를 공격 쪽으로 반만큼 끌어간다
            #   (R1 산출물 재연: 채택 NONE 952 중 165행이 kanana 최상위 INJECTION). 채택 라벨과
            #   모순되는 교사 soft target 은 그 행에서 끈다.
            #   JAILBREAK 행은 kanana 분포에 그 칸이 없어(3클래스 제한 KL) 모순이 정의되지
            #   않으므로 둔다.
            if (
                soft_probabilities is not None
                and decision_label in SOFT_TARGET_CLASSES
                and SOFT_TARGET_CLASSES[
                    max(range(len(soft_probabilities)), key=soft_probabilities.__getitem__)
                ] != decision_label
            ):
                soft_probabilities = None
                silenced.append("soft")
            if jailbreak_target is not None and (jailbreak_target >= 0.5) != (
                decision_label == "JAILBREAK"
            ):
                jailbreak_target = None
                silenced.append("jailbreak")
        audit[-1]["silenced_soft_targets"] = silenced
        accepted.append(
            record.model_copy(
                update={
                    "attack_label": decision_label,
                    "kanana_probabilities": soft_probabilities,
                    "qwen_jailbreak_target": jailbreak_target,
                    "meta": {
                        **record.meta,
                        "requires_teacher_label": False,
                        "teacher_resolution": "class-specific-two-vote",
                    },
                }
            )
        )
    # 분할은 합의된 행 전체를 보고 정한다 — 행 하나만 보면 template 이 갈린다.
    splits = component_validation_splits(accepted, validation_percent)
    accepted = [record.model_copy(update={"split": splits[record.id]}) for record in accepted]
    # ★ 여기서 한 번 걸러야 한다. 버킷 분할이 group/template 을 쪼개면
    #   `validate_corpus` 가 거부하는 코퍼스가 만들어지는데, 그 실패가 지금까지는
    #   다음 로드까지 미뤄졌다. 만든 자리에서 터뜨린다.
    if accepted:
        validate_corpus(accepted)
    return accepted, review, audit
