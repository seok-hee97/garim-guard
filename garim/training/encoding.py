"""Character-offset-first BIO alignment for Korean PII training examples."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER
from garim.normalize.chain import DELTA_KEYS
from garim.training.export import PII_LABELS

_LABEL_ID = {label: index for index, label in enumerate(PII_LABELS)}
_ENTITY_LABELS = {label[2:] for label in PII_LABELS if label.startswith("B-")}


@dataclass(frozen=True, slots=True)
class EntitySpan:
    label: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.label not in _ENTITY_LABELS:
            raise ValueError(f"unsupported PII label: {self.label}")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("entity span must be non-empty and non-negative")


def normalization_delta_vector(delta: dict[str, int]) -> list[float]:
    """Match the runtime ONNX adapter's fixed order and log1p transformation.

    입력은 §3.2.5 **스키마 전체**(8칸)여야 하고 출력은 §6.2.2 **feature 계약**(7칸)이다.
    두 집합이 다른 이유는 `NORMALIZATION_DELTA_ORDER` 옆에 적혀 있다 — `space` 는
    정규화가 지우지 않는 유일한 칸이라 feature 자격이 없다.

    스키마 쪽을 느슨하게 두면 안 된다. 칸이 하나 빠진 델타를 받아도 조용히 7칸을
    만들어내면 학습과 추론이 서로 다른 것을 먹으면서 아무도 모른다.
    """
    missing = set(DELTA_KEYS) - delta.keys()
    extra = delta.keys() - set(DELTA_KEYS)
    if missing or extra:
        raise ValueError(f"normalization delta keys mismatch: missing={missing}, extra={extra}")
    if any(delta[key] < 0 for key in DELTA_KEYS):
        raise ValueError("normalization delta counts must be non-negative")
    return [math.log1p(delta[key]) for key in NORMALIZATION_DELTA_ORDER]


def align_bio_labels(offsets: list[tuple[int, int]], entity_spans: list[EntitySpan]) -> list[int]:
    """Map character spans to token BIO ids; special/padding tokens receive -100."""
    entities = sorted(entity_spans, key=lambda entity: (entity.start, entity.end))
    for left, right in zip(entities, entities[1:], strict=False):
        if left.end > right.start:
            raise ValueError("PII entity spans must not overlap")

    labels: list[int] = []
    covered: set[int] = set()
    previous_entity: int | None = None
    for token_start, token_end in offsets:
        if token_start == token_end:
            labels.append(-100)
            previous_entity = None
            continue
        overlaps = [
            index
            for index, entity in enumerate(entities)
            if token_start < entity.end and entity.start < token_end
        ]
        if len(overlaps) > 1:
            raise ValueError("one tokenizer token overlaps multiple PII entities")
        if not overlaps:
            labels.append(_LABEL_ID["O"])
            previous_entity = None
            continue
        entity_index = overlaps[0]
        entity = entities[entity_index]
        prefix = "I" if previous_entity == entity_index else "B"
        labels.append(_LABEL_ID[f"{prefix}-{entity.label}"])
        covered.add(entity_index)
        previous_entity = entity_index

    missing = [entity for index, entity in enumerate(entities) if index not in covered]
    if missing:
        raise ValueError(
            "tokenizer offsets do not cover every entity; pre-chunk instead of truncating"
        )
    return labels


def encode_pii_example(
    tokenizer: Any,
    text: str,
    entity_spans: list[EntitySpan],
    *,
    max_length: int = 512,
) -> dict[str, Any]:
    """Tokenize one pre-chunked example and attach 29-class BIO labels."""
    if any(entity.end > len(text) for entity in entity_spans):
        raise ValueError("entity span is outside source text")
    encoded = tokenizer(
        text,
        truncation=True,
        max_length=max_length,
        return_offsets_mapping=True,
    )
    offsets = [tuple(map(int, offset)) for offset in encoded.pop("offset_mapping")]
    encoded["pii_labels"] = align_bio_labels(offsets, entity_spans)
    return encoded
