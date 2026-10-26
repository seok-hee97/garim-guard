"""Blinded labeling exports and exact-coverage human gold application."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from garim.training.data import CorpusRecord, validate_corpus
from garim.training.readiness import validate_sealed_evaluation_corpus
from garim.training.split import component_validation_splits
from garim.training.teachers import AttackLabel


class ManualLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: AttackLabel
    reviewer: str
    guide_version: str


def load_manual_labels(path: str | Path) -> dict[str, ManualLabel]:
    source = Path(path)
    labels: dict[str, ManualLabel] = {}
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                decision = ManualLabel.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: {exc}") from exc
            if not decision.reviewer.strip() or not decision.guide_version.strip():
                raise ValueError(f"{source}:{line_number}: reviewer and guide_version required")
            if decision.id in labels:
                raise ValueError(f"duplicate manual label id: {decision.id}")
            labels[decision.id] = decision
    return labels


def blind_labeling_rows(
    records: Sequence[CorpusRecord], *, purpose: str
) -> list[dict[str, str]]:
    """Expose only text and stable identity; source labels, teachers, and metadata stay hidden."""
    if purpose not in {"teacher-review", "final-evaluation"}:
        raise ValueError("purpose must be teacher-review or final-evaluation")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for record in records:
        if record.id in seen:
            raise ValueError(f"duplicate labeling queue id: {record.id}")
        seen.add(record.id)
        if record.task != "attack" or record.attack_label is not None:
            raise ValueError(f"{record.id}: blind queue requires unlabeled attack rows")
        if purpose == "teacher-review" and record.split != "train":
            raise ValueError(f"{record.id}: teacher review source must remain split=train")
        if purpose == "final-evaluation" and record.split != "test":
            raise ValueError(f"{record.id}: final evaluation source must be split=test")
        rows.append({"id": record.id, "text": record.text})
    if not rows:
        raise ValueError("blind labeling queue is empty")
    return rows


def _assert_exact_coverage(
    records: Sequence[CorpusRecord], labels: dict[str, ManualLabel], *, what: str
) -> None:
    expected = {record.id for record in records}
    if len(expected) != len(records):
        raise ValueError(f"{what} inputs contain duplicate IDs")
    if expected != labels.keys():
        raise ValueError(
            f"{what} label IDs mismatch: missing={len(expected - labels.keys())}, "
            f"extra={len(labels.keys() - expected)}"
        )


def apply_review_labels(
    records: Sequence[CorpusRecord],
    labels: dict[str, ManualLabel],
    *,
    validation_percent: int = 10,
) -> list[CorpusRecord]:
    """Adjudicate teacher-review rows and re-cut train/validation with the shared bucket.

    This shares ``ManualLabel`` with the final-evaluation path and the component split
    with teacher resolution on purpose. The human-label entry points previously carried
    independent JSONL parsers and independent copies of the bucket arithmetic, so a change
    to one silently diverged from the other.
    """
    _assert_exact_coverage(records, labels, what="teacher review")
    # 교사 합의분(`resolve_teacher_corpus`)과 같은 규칙이어야 두 파일을 합쳐 봉인할 때
    # 한 template 이 양쪽으로 갈리지 않는다.
    splits = component_validation_splits(records, validation_percent)
    output: list[CorpusRecord] = []
    for record in records:
        if record.task != "attack" or record.attack_label is not None:
            raise ValueError(f"{record.id}: expected an unlabeled attack row")
        decision = labels[record.id]
        split = splits[record.id]
        output.append(
            record.model_copy(
                update={
                    "attack_label": decision.label,
                    "split": split,
                    "meta": {
                        **record.meta,
                        "requires_teacher_label": False,
                        "human_review": {
                            "reviewer": decision.reviewer,
                            "guide_version": decision.guide_version,
                            "blinded": True,
                        },
                    },
                }
            )
        )
    validate_corpus(output)
    return output


def apply_evaluation_labels(
    records: Sequence[CorpusRecord], labels: dict[str, ManualLabel]
) -> list[CorpusRecord]:
    _assert_exact_coverage(records, labels, what="evaluation")
    output: list[CorpusRecord] = []
    for record in records:
        if record.task != "attack" or record.split != "test" or record.attack_label is not None:
            raise ValueError(f"{record.id}: expected an unlabeled split=test attack row")
        decision = labels[record.id]
        output.append(
            record.model_copy(
                update={
                    "attack_label": decision.label,
                    "meta": {
                        **record.meta,
                        "requires_manual_label": False,
                        "human_gold": {
                            "reviewer": decision.reviewer,
                            "guide_version": decision.guide_version,
                            "blinded": True,
                        },
                    },
                }
            )
        )
    validate_corpus(output)
    validate_sealed_evaluation_corpus(output)
    return output


def write_blind_labeling_rows(path: str | Path, rows: Sequence[dict[str, str]]) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(destination)
    return len(rows)
