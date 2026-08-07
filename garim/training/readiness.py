"""Fail-closed semantic gates shared by preflight, training, and final evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from garim.training.data import CorpusRecord, validate_corpus

_UNRESOLVED_FLAGS = (
    "requires_translation",
    "requires_teacher_label",
    "requires_manual_label",
)


def validate_training_corpora(
    pii_train: Sequence[CorpusRecord],
    pii_validation: Sequence[CorpusRecord],
    attack_train: Sequence[CorpusRecord],
    attack_validation: Sequence[CorpusRecord],
) -> dict[str, Any]:
    """Validate the exact four corpus groups accepted by the joint trainer."""
    groups = {
        "pii_train": pii_train,
        "pii_validation": pii_validation,
        "attack_train": attack_train,
        "attack_validation": attack_validation,
    }
    for name, records in groups.items():
        if not records:
            raise ValueError(f"{name} is empty")
        expected_task = "pii" if name.startswith("pii") else "attack"
        expected_split = "train" if name.endswith("train") else "validation"
        for record in records:
            if record.task != expected_task or record.split != expected_split:
                raise ValueError(f"{record.id}: wrong task/split for {name}")
            if record.task == "attack" and record.attack_label is None:
                raise ValueError(f"{record.id}: unresolved attack label")
            unresolved = [flag for flag in _UNRESOLVED_FLAGS if record.meta.get(flag)]
            if unresolved:
                raise ValueError(
                    f"{record.id}: unresolved training metadata: {', '.join(unresolved)}"
                )
    all_records = [record for records in groups.values() for record in records]
    report = validate_corpus(all_records)
    report["files"] = {name: len(records) for name, records in groups.items()}
    return report


def validate_sealed_evaluation_corpus(
    records: Sequence[CorpusRecord],
) -> dict[str, Any]:
    """Reject unsealed or unresolved rows before the one-shot final evaluator runs."""
    if not records:
        raise ValueError("evaluation corpus is empty")
    for record in records:
        if record.split != "test":
            raise ValueError(f"{record.id}: final evaluation requires split=test")
        unresolved = [flag for flag in _UNRESOLVED_FLAGS if record.meta.get(flag)]
        if unresolved:
            raise ValueError(
                f"{record.id}: unresolved evaluation metadata: {', '.join(unresolved)}"
            )
        if record.task == "attack" and record.attack_label is None:
            raise ValueError(f"{record.id}: attack test row has no manual gold label")
    return validate_corpus(records)
