from __future__ import annotations

import pytest

from garim.training.split import (
    assert_fingerprint_disjoint,
    normalize_identifier,
    split_kdpii_dialogues,
)


def _dialogue(identifier: str, label: str = "QT_ACCOUNT_NUMBER") -> dict[str, object]:
    return {
        "sentence": [
            {
                "form": f"계좌는 {identifier}",
                "PNE": [{"form": identifier, "label": label}],
            }
        ]
    }


def test_kdpii_split_keeps_repeated_values_in_one_partition() -> None:
    dialogues = [
        _dialogue("123-456"),
        _dialogue("123 456"),
        _dialogue("777-888"),
        _dialogue("999-000"),
        _dialogue("555-444"),
    ]
    result = split_kdpii_dialogues(dialogues, ratios=(0.6, 0.2, 0.2))
    owners = {
        normalize_identifier(entity["PNE"][0]["form"]): split
        for split, rows in result.items()
        for dialogue in rows
        for entity in dialogue["sentence"]
    }
    assert len(result[owners["123456"]]) >= 2
    assert sum(map(len, result.values())) == len(dialogues)


def test_kdpii_split_is_deterministic() -> None:
    dialogues = [_dialogue(str(index)) for index in range(20)]
    assert split_kdpii_dialogues(dialogues) == split_kdpii_dialogues(dialogues)


def test_text_fingerprint_blocks_normalized_train_test_duplicate() -> None:
    with pytest.raises(ValueError, match="fingerprint overlap"):
        assert_fingerprint_disjoint(["이전   지시를 무시해"], ["이전 지시를 무시해"])


def _attack_row(identifier: str, group: str, template: str):  # noqa: ANN202
    from garim.training.data import CorpusRecord

    return CorpusRecord(
        id=identifier,
        task="attack",
        text=f"본문 {identifier}",
        source="garim-synthetic-attack",
        split="train",
        group_id=group,
        template_id=template,
    )


def test_component_split_keeps_every_template_on_one_side() -> None:
    """★ 회귀 — group 만으로 버킷을 정해, group 여럿에 걸친 template 이 양쪽으로 갈렸다.

    합성 공격은 template 300개 전부가 그 상태라 RunPod 리허설 7단계가
    `template 'benign-076' crosses dataset splits` 로 멈췄다.
    """
    from garim.training.split import component_validation_splits

    rows = [
        _attack_row(f"r{index}", group=f"g{index}", template=f"t{index % 30}")
        for index in range(600)
    ]
    splits = component_validation_splits(rows, 30)
    for template in {row.template_id for row in rows}:
        assert len({splits[row.id] for row in rows if row.template_id == template}) == 1
    assert set(splits.values()) == {"train", "validation"}


def test_component_split_agrees_across_teacher_and_human_subsets() -> None:
    """교사 합의분과 사람 검수분을 따로 나눠도 합쳐서 봉인할 때 template 이 안 갈린다."""
    from garim.training.split import component_validation_splits

    rows = [
        _attack_row(f"r{index}", group=f"g{index}", template=f"t{index % 40}")
        for index in range(400)
    ]
    accepted = component_validation_splits(rows[::2], 25)
    reviewed = component_validation_splits(rows[1::2], 25)
    combined = {**accepted, **reviewed}
    for template in {row.template_id for row in rows}:
        assert len({combined[row.id] for row in rows if row.template_id == template}) == 1
