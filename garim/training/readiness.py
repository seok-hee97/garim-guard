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
    report["teacher_coverage"] = teacher_coverage(attack_train)
    return report


def teacher_coverage(attack_records: Sequence[CorpusRecord]) -> dict[str, float]:
    """공격 학습 행 중 교사 라벨을 가진 비율."""
    total = len(attack_records) or 1
    return {
        "kanana": sum(r.kanana_probabilities is not None for r in attack_records) / total,
        "qwen_jailbreak": sum(r.qwen_jailbreak_target is not None for r in attack_records) / total,
    }


def assert_distillation_inputs_exist(
    attack_train: Sequence[CorpusRecord],
    *,
    hard_label_alpha: float,
    qwen_weight: float,
) -> None:
    """★ 증류 계수를 켜 두고 교사 라벨 없이 도는 것을 막는다.

    `losses.py` 는 `kanana_mask` 로 결측을 넘긴다 — 라벨이 하나도 없으면 KL 항이 통째로
    0 이 되고 **오류 없이** 하드 라벨 CE 만 남는다. 학습은 끝나고 체크포인트도 나오고
    지표도 나오는데 §6.2.2 의 증류가 한 번도 안 돈다. 아무도 모른다.

    실제로 그 상태였다 — 봉인 직후 공격 학습 1,238행 중 kanana 확률 0행, qwen 타깃 0행.

    계수가 증류를 요구하면(`hard_label_alpha < 1` 또는 `qwen_weight > 0`) 대응하는 교사
    라벨이 실제로 있어야 시작한다. 증류 없이 돌리려면 계수를 그렇게 **적어야** 한다 —
    설정과 실행이 어긋난 채로 도는 경우를 없애는 것이 요점이다.
    """
    coverage = teacher_coverage(attack_train)
    missing = []
    if hard_label_alpha < 1.0 and not coverage["kanana"]:
        missing.append(
            f"hard_label_alpha={hard_label_alpha} 는 kanana 소프트 타깃을 요구하는데 "
            f"공격 학습 {len(attack_train)}행 중 0행이 갖고 있다"
        )
    if qwen_weight > 0.0 and not coverage["qwen_jailbreak"]:
        missing.append(
            f"qwen_weight={qwen_weight} 는 Qwen jailbreak 타깃을 요구하는데 "
            f"공격 학습 {len(attack_train)}행 중 0행이 갖고 있다"
        )
    if missing:
        raise ValueError(
            "distillation is configured but its teacher labels are absent — "
            "run train/run_teacher.py first, or set the weights to disable it:\n  "
            + "\n  ".join(missing)
        )


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
