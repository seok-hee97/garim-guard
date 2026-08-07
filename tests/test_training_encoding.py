from __future__ import annotations

import pytest

from garim.training.encoding import (
    EntitySpan,
    align_bio_labels,
    encode_pii_example,
    normalization_delta_vector,
)
from garim.training.export import PII_LABELS


def test_character_spans_align_to_bio_tokens_and_ignore_specials() -> None:
    offsets = [(0, 0), (0, 1), (1, 3), (3, 4), (0, 0)]
    labels = align_bio_labels(offsets, [EntitySpan("PERSON", 0, 3)])
    assert labels == [
        -100,
        PII_LABELS.index("B-PERSON"),
        PII_LABELS.index("I-PERSON"),
        PII_LABELS.index("O"),
        -100,
    ]


def test_alignment_rejects_entity_silently_lost_by_truncation() -> None:
    with pytest.raises(ValueError, match="pre-chunk"):
        align_bio_labels([(0, 2)], [EntitySpan("PERSON", 5, 8)])


def test_encode_removes_offsets_from_model_inputs() -> None:
    class Tokenizer:
        def __call__(self, text, **kwargs):  # noqa: ANN001
            assert kwargs["return_offsets_mapping"]
            return {
                "input_ids": [1, 2, 3],
                "attention_mask": [1, 1, 1],
                "offset_mapping": [(0, 0), (0, len(text)), (0, 0)],
            }

    encoded = encode_pii_example(Tokenizer(), "홍길동", [EntitySpan("PERSON", 0, 3)])
    assert "offset_mapping" not in encoded
    assert encoded["pii_labels"] == [-100, PII_LABELS.index("B-PERSON"), -100]


def test_normalization_delta_matches_runtime_order_and_log_scale() -> None:
    delta = {
        "nfkc": 0,
        "zero_width": 1,
        "tag_chars": 0,
        "homoglyph": 0,
        "jamo_composed": 0,
        "hanja": 0,
        "space": 3,
        "b64_decoded": 0,
    }
    vector = normalization_delta_vector(delta)
    assert vector[1] > 0
    assert vector[6] > vector[1]
