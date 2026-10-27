"""Class-specific teacher eligibility and deterministic pseudo-label resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

AttackLabel = Literal["NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK"]
ATTACK_LABELS = ("NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK")

# ★ 증류 역할은 **soft_targets 내용**으로 정한다 — 교사 이름으로 정하지 않는다.
#   `teacher_io` 가 `row_artifacts["kanana"]` / `["qwen3guard_gen"]` 로 이름을 네 군데
#   박아 두어, `teachers.yaml` 에서 키만 바꿔도 로드가 아니라 해석 도중 KeyError 였다.
#   정책이 설정 가능하다고 선언했으면 코드도 설정을 따라야 한다.
SOFT_TARGET_CLASSES = ("NONE", "INJECTION", "SYSTEM_LEAK")
JAILBREAK_POSTERIOR_CLASSES = ("JAILBREAK",)


class TeacherSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    revision: str
    votes_for: list[AttackLabel]
    soft_targets: list[AttackLabel] = Field(default_factory=list)
    can_flip_none: bool = False


class TeacherPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classes: list[AttackLabel]
    minimum_votes: int = Field(2, ge=2)
    minimum_confidence: float = Field(0.5, ge=0.0, le=1.0)
    teachers: dict[str, TeacherSpec]

    @model_validator(mode="after")
    def validate_coverage(self) -> TeacherPolicy:
        if tuple(self.classes) != ATTACK_LABELS:
            raise ValueError(f"classes must be exactly {ATTACK_LABELS}")
        for label in ATTACK_LABELS:
            voters = sum(label in teacher.votes_for for teacher in self.teachers.values())
            if voters < self.minimum_votes:
                raise ValueError(f"{label} has only {voters} eligible teachers")
        for name, teacher in self.teachers.items():
            if len(set(teacher.soft_targets)) != len(teacher.soft_targets):
                raise ValueError(f"{name}: duplicate soft target class")
            if not set(teacher.soft_targets) <= set(teacher.votes_for):
                raise ValueError(f"{name}: soft targets must be a subset of votes_for")
        for role in (SOFT_TARGET_CLASSES, JAILBREAK_POSTERIOR_CLASSES):
            owners = self._teachers_with_soft_targets(role)
            if len(owners) > 1:
                raise ValueError(
                    f"soft-target role {role} is claimed by more than one teacher: {owners}"
                )
        return self

    def _teachers_with_soft_targets(self, classes: tuple[AttackLabel, ...]) -> list[str]:
        return sorted(
            name
            for name, teacher in self.teachers.items()
            if set(teacher.soft_targets) == set(classes)
        )

    def soft_target_teacher(self, classes: tuple[AttackLabel, ...]) -> str | None:
        """Return the single teacher declared to emit exactly `classes`, if any."""
        owners = self._teachers_with_soft_targets(classes)
        return owners[0] if owners else None


@dataclass(frozen=True, slots=True)
class TeacherPrediction:
    label: AttackLabel
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("teacher confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class TeacherDecision:
    label: AttackLabel | None
    status: Literal["accepted", "review"]
    votes: dict[AttackLabel, int]
    reason: str | None = None


def load_teacher_policy(path: str | Path = "train/teachers.yaml") -> TeacherPolicy:
    policy_path = Path(path)
    try:
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load teacher policy {policy_path}: {exc}") from exc
    return TeacherPolicy.model_validate(payload)


def resolve_teacher_label(
    predictions: dict[str, TeacherPrediction],
    policy: TeacherPolicy,
    *,
    generator_label: AttackLabel | None = None,
) -> TeacherDecision:
    """클래스별 2표 합의. `generator_label` 은 생성기가 만든 행에만 준다(PREREG_L1_R2 C3).

    ★ R1 에서 2표 합의가 생성기 정상문 두 문장을 통째로 폐기했다 — 교사 한쪽의 과탐이
      불일치가 되고, 불일치는 폐기라 학생이 그 문장을 NONE 으로 한 번도 못 봤다(RESULTS_L1
      §5-②). 정상이 되도록 쓴 행이므로 생성기가 NONE 에 한 표를 더한다. 공격 쪽은 투표하지
      않는다 — 자작 공격이 교사 검증을 우회하면 안 된다. 대신 교사 합의가 생성기 라벨과
      다르면 폐기한다. 은닉 때문에 교사가 못 읽은 자작 공격이 NONE 으로 학습되던 자리다.
    """
    votes: dict[AttackLabel, int] = {label: 0 for label in ATTACK_LABELS}
    for teacher_name, prediction in predictions.items():
        teacher = policy.teachers.get(teacher_name)
        if teacher is None:
            raise ValueError(f"unknown teacher: {teacher_name}")
        if (
            prediction.label in teacher.votes_for
            and prediction.confidence >= policy.minimum_confidence
        ):
            votes[prediction.label] += 1
    if generator_label == "NONE":
        votes["NONE"] += 1

    best_count = max(votes.values())
    winners = [label for label, count in votes.items() if count == best_count]
    if best_count < policy.minimum_votes or len(winners) != 1:
        return TeacherDecision(None, "review", votes, "no_consensus")
    if generator_label is not None and winners[0] != generator_label:
        return TeacherDecision(None, "review", votes, "generator_conflict")
    return TeacherDecision(winners[0], "accepted", votes)
