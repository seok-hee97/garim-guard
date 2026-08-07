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
