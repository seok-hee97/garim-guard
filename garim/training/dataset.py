"""Tokenization, overflow windows, and dynamic padding for the joint L1 trainer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from garim.normalize.chain import normalize
from garim.training.data import CorpusRecord
from garim.training.encoding import EntitySpan, align_bio_labels, normalization_delta_vector
from garim.training.export import PII_LABELS
from garim.training.teachers import ATTACK_LABELS

_ATTACK_ID = {label: index for index, label in enumerate(ATTACK_LABELS)}
_O_LABEL_ID = PII_LABELS.index("O")


def _normalized_spans(record: CorpusRecord) -> tuple[str, list[EntitySpan], list[float]]:
    bundle = normalize(record.text)
    spans: list[EntitySpan] = []
    for source in record.spans:
        indexes = [
            index
            for index, (raw_start, raw_end) in enumerate(bundle.norm.interval_map)
            if raw_start < source.end and source.start < raw_end
        ]
        if not indexes:
            raise ValueError(f"{record.id}: normalization removed an entire PII span")
        spans.append(EntitySpan(source.label, min(indexes), max(indexes) + 1))
    return bundle.norm.text, spans, normalization_delta_vector(bundle.delta)


def encode_corpus_record(
    tokenizer: Any,
    record: CorpusRecord,
    *,
    max_length: int = 512,
    stride: int = 64,
) -> list[dict[str, Any]]:
    """Encode one record into one or more runtime-equivalent overflow windows."""
    text, entity_spans, delta = _normalized_spans(record)
    encoded = tokenizer(
        text,
        truncation=True,
        max_length=max_length,
        stride=stride,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
    )
    input_ids = encoded["input_ids"]
    if input_ids and isinstance(input_ids[0], int):
        windows = [
            {
                key: value
                for key, value in encoded.items()
                if key not in {"overflow_to_sample_mapping", "num_truncated_tokens"}
            }
        ]
    else:
        window_count = len(input_ids)
        windows = []
        for index in range(window_count):
            window: dict[str, Any] = {}
            for key, value in encoded.items():
                if key in {"overflow_to_sample_mapping", "num_truncated_tokens"}:
                    continue
                if isinstance(value, list) and len(value) == window_count:
                    window[key] = value[index]
            windows.append(window)

    covered_entities: set[int] = set()
    output: list[dict[str, Any]] = []
    for window_index, window in enumerate(windows):
        offsets = [tuple(map(int, pair)) for pair in window.pop("offset_mapping")]
        real_offsets = [pair for pair in offsets if pair[0] != pair[1]]
        char_start = min((pair[0] for pair in real_offsets), default=0)
        char_end = max((pair[1] for pair in real_offsets), default=0)
        selected: list[EntitySpan] = []
        for entity_index, entity in enumerate(entity_spans):
            if char_start <= entity.start and entity.end <= char_end:
                selected.append(entity)
                covered_entities.add(entity_index)
        pii_labels = (
            align_bio_labels(offsets, selected)
            if record.task == "pii"
            else [-100] * len(offsets)
        )
        # ★ 손실과 지표를 분리한다. §6.0 은 공격 행에서 PII 손실을 마스킹하라고 하고
        #   `pii_labels` 가 그것을 한다. 그런데 평가까지 같은 텐서를 쓰면 공격 텍스트
        #   위의 PII 오탐이 `predicted_chars` 에 아예 들어가지 않아, §7.1.1 이 헤드라인으로
        #   내건 FPR 을 early stopping 이 못 보고 돌았다. 평가용은 공격 행의 실토큰을
        #   `O` 로 둬서 오탐이 precision 에 잡히게 한다 — 학습 마스킹은 그대로다.
        pii_eval_labels = (
            pii_labels
            if record.task == "pii"
            else [-100 if start == end else _O_LABEL_ID for start, end in offsets]
        )
        window.update(
            {
                "pii_labels": pii_labels,
                "pii_eval_labels": pii_eval_labels,
                "attack_labels": (
                    _ATTACK_ID[record.attack_label]
                    if record.attack_label is not None
                    else -100
                ),
                "normalization_delta": delta,
                "kanana_probabilities": list(record.kanana_probabilities or (0.0, 0.0, 0.0)),
                "kanana_mask": record.kanana_probabilities is not None,
                "qwen_jailbreak_targets": (
                    record.qwen_jailbreak_target
                    if record.qwen_jailbreak_target is not None
                    else -1.0
                ),
                "romanized_targets": (
                    record.romanized_target if record.romanized_target is not None else -1.0
                ),
                "honorific_targets": (
                    record.honorific_target if record.honorific_target is not None else -1.0
                ),
                "record_id": record.id,
                "window_index": window_index,
                "offset_mapping": offsets,
                "gold_spans": [span.model_dump() for span in record.spans],
                "normalized_text": text,
            }
        )
        output.append(window)
    missing = set(range(len(entity_spans))) - covered_entities
    if missing:
        raise ValueError(
            f"{record.id}: {len(missing)} entities do not fit in a {max_length}-token window"
        )
    return output


class EncodedCorpusDataset:
    """Eagerly tokenized dataset so alignment errors fail before optimizer creation."""

    def __init__(
        self,
        tokenizer: Any,
        records: Sequence[CorpusRecord],
        *,
        max_length: int = 512,
        stride: int = 64,
    ) -> None:
        self.examples = [
            example
            for record in records
            for example in encode_corpus_record(
                tokenizer, record, max_length=max_length, stride=stride
            )
        ]
        if not self.examples:
            raise ValueError("encoded corpus is empty")

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.examples[index]


class JointBatchCollator:
    def __init__(self, tokenizer: Any) -> None:
        self.pad_token_id = tokenizer.pad_token_id
        if self.pad_token_id is None:
            raise ValueError("L1 tokenizer must define pad_token_id")

    def __call__(self, examples: Sequence[dict[str, Any]]) -> dict[str, Any]:
        import torch

        max_length = max(len(example["input_ids"]) for example in examples)

        def padded(key: str, fill: int) -> list[list[int]]:
            return [
                list(example[key]) + [fill] * (max_length - len(example[key]))
                for example in examples
            ]

        batch: dict[str, Any] = {
            "input_ids": torch.tensor(padded("input_ids", self.pad_token_id), dtype=torch.long),
            "attention_mask": torch.tensor(padded("attention_mask", 0), dtype=torch.long),
            "pii_labels": torch.tensor(padded("pii_labels", -100), dtype=torch.long),
            "pii_eval_labels": torch.tensor(
                padded("pii_eval_labels", -100), dtype=torch.long
            ),
            "attack_labels": torch.tensor(
                [example["attack_labels"] for example in examples], dtype=torch.long
            ),
            "normalization_delta": torch.tensor(
                [example["normalization_delta"] for example in examples], dtype=torch.float32
            ),
            "kanana_probabilities": torch.tensor(
                [example["kanana_probabilities"] for example in examples], dtype=torch.float32
            ),
            "kanana_mask": torch.tensor(
                [example["kanana_mask"] for example in examples], dtype=torch.bool
            ),
            "qwen_jailbreak_targets": torch.tensor(
                [example["qwen_jailbreak_targets"] for example in examples], dtype=torch.float32
            ),
            "romanized_targets": torch.tensor(
                [example["romanized_targets"] for example in examples], dtype=torch.float32
            ),
            "honorific_targets": torch.tensor(
                [example["honorific_targets"] for example in examples], dtype=torch.float32
            ),
            "record_ids": [example["record_id"] for example in examples],
            "window_indexes": [example["window_index"] for example in examples],
            "offset_mappings": [example["offset_mapping"] for example in examples],
        }
        if all("token_type_ids" in example for example in examples):
            batch["token_type_ids"] = torch.tensor(
                padded("token_type_ids", 0), dtype=torch.long
            )
        return batch
