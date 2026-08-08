from __future__ import annotations

import math

import pytest

from garim.detect.l1 import (
    DELTA_KEYS_EXCLUDED_FROM_FEATURES,
    NORMALIZATION_DELTA_ORDER,
)
from garim.normalize.chain import DELTA_KEYS
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
    # 입력은 §3.2.5 스키마 8칸, 출력은 §6.2.2 feature 7칸이다.
    assert len(delta) == 8
    assert len(vector) == len(NORMALIZATION_DELTA_ORDER) == 7
    assert vector == [math.log1p(delta[key]) for key in NORMALIZATION_DELTA_ORDER]
    assert vector[NORMALIZATION_DELTA_ORDER.index("zero_width")] > 0


def test_space_is_in_the_schema_but_not_in_the_feature_vector() -> None:
    """★ `space` 를 feature 에서 뺀 결정을 고정한다(§6.2.2).

    빼는 이유가 셋인데 마지막이 결정적이다.

    1. 비단조다 — 공백 **제거** 우회 0 / 평범한 한국어 6~8 / 공백 **삽입** 우회 20 이라
       평범한 글이 두 공격 방향 사이에 있다.
    2. 이 칸만 다른 스트림을 기술한다. `norm` 은 공백을 보존하고 `space` 는 `nospace`
       뷰를 만들며 지운 개수다. L1 은 `nospace` 를 받지 않는다.
    3. 나머지 일곱은 **정규화가 지운** 정보를 나르므로 그 숫자가 유일한 채널이다.
       공백은 텍스트에 그대로 남아 토크나이저가 이미 본다 — 채널이 아니라 지름길이다.

    스키마에서는 빼지 않는다. A-KO-05 배출과 `nospace` 재검사를 켜는 L0 신호다.
    """
    assert DELTA_KEYS_EXCLUDED_FROM_FEATURES == ("space",)
    assert set(NORMALIZATION_DELTA_ORDER) | set(DELTA_KEYS_EXCLUDED_FROM_FEATURES) == set(
        DELTA_KEYS
    )
    assert "space" in DELTA_KEYS
    assert "space" not in NORMALIZATION_DELTA_ORDER


def test_delta_missing_a_schema_key_is_refused() -> None:
    """스키마를 느슨하게 받으면 학습과 추론이 다른 것을 먹으면서 아무도 모른다."""
    incomplete = dict.fromkeys(NORMALIZATION_DELTA_ORDER, 0)
    with pytest.raises(ValueError, match="missing=.*space"):
        normalization_delta_vector(incomplete)
