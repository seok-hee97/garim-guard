"""Held-out corpus evaluation through the deployable ONNX runtime contract."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from garim.detect.l1 import OnnxL1Backend
from garim.normalize.chain import normalize
from garim.normalize.offsets import to_source_span
from garim.segment.chunk import chunk_text
from garim.training.data import CorpusRecord
from garim.training.teachers import ATTACK_LABELS


def _divide(left: int, right: int) -> float:
    return left / right if right else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evaluate_onnx_records(
    backend: OnnxL1Backend, records: list[CorpusRecord], *, chunk_overlap: int = 64
) -> dict[str, Any]:
    gold_chars: set[tuple[str, int, str]] = set()
    predicted_chars: set[tuple[str, int, str]] = set()
    attack_confusion = {
        gold: {predicted: 0 for predicted in ATTACK_LABELS} for gold in ATTACK_LABELS
    }

    for record in records:
        bundle = normalize(record.text)
        chunks = chunk_text(
            bundle.norm.text,
            chunk_size=backend.max_length,
            chunk_overlap=chunk_overlap,
            tokenizer=backend.tokenizer,
        )
        attack_candidates: list[tuple[str, float]] = []
        for chunk in chunks:
            output = backend.infer(
                chunk.text,
                normalization_delta=bundle.delta,
                origin="user",
                content_type=str(record.meta.get("doc_shape", "prose"))
                if record.meta.get("doc_shape") in {"prose", "code", "structured", "log", "unknown"}
                else "prose",  # type: ignore[arg-type]
            )
            for entity in output.entities:
                normalized_span = (
                    entity.span[0] + chunk.start,
                    entity.span[1] + chunk.start,
                )
                raw_start, raw_end = to_source_span(
                    normalized_span, bundle.norm.interval_map, len(record.text)
                )
                for position in range(raw_start, raw_end):
                    predicted_chars.add((record.id, position, entity.label))
            attack_candidates.append(
                max(output.attack_probabilities.items(), key=lambda item: item[1])
            )

        for span in record.spans:
            for position in range(span.start, span.end):
                gold_chars.add((record.id, position, span.label))
        if record.attack_label is not None:
            non_none = [item for item in attack_candidates if item[0] != "NONE"]
            prediction = max(non_none, key=lambda item: item[1])[0] if non_none else "NONE"
            attack_confusion[record.attack_label][prediction] += 1

    labels = sorted({item[2] for item in gold_chars | predicted_chars})
    pii_by_label: dict[str, dict[str, float | int]] = {}
    for label in labels:
        gold = {item for item in gold_chars if item[2] == label}
        predicted = {item for item in predicted_chars if item[2] == label}
        true_positive = len(gold & predicted)
        precision = _divide(true_positive, len(predicted))
        recall = _divide(true_positive, len(gold))
        pii_by_label[label] = {
            "gold_characters": len(gold),
            "predicted_characters": len(predicted),
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }

    macro_precision: list[float] = []
    macro_recall: list[float] = []
    macro_f1: list[float] = []
    for label in ATTACK_LABELS:
        true_positive = attack_confusion[label][label]
        actual = sum(attack_confusion[label].values())
        predicted = sum(attack_confusion[gold][label] for gold in ATTACK_LABELS)
        precision = _divide(true_positive, predicted)
        recall = _divide(true_positive, actual)
        macro_precision.append(precision)
        macro_recall.append(recall)
        macro_f1.append(_f1(precision, recall))
    none_total = sum(attack_confusion["NONE"].values())
    return {
        "records": len(records),
        "pii_character_by_label": pii_by_label,
        "attack_confusion": attack_confusion,
        "attack_macro_precision": sum(macro_precision) / len(macro_precision),
        "attack_macro_recall": sum(macro_recall) / len(macro_recall),
        "attack_macro_f1": sum(macro_f1) / len(macro_f1),
        "attack_false_positive_rate": _divide(
            none_total - attack_confusion["NONE"]["NONE"], none_total
        ),
    }


def evaluate_onnx_by_source(backend: OnnxL1Backend, records: list[CorpusRecord]) -> dict[str, Any]:
    grouped: dict[str, list[CorpusRecord]] = defaultdict(list)
    for record in records:
        grouped[record.source].append(record)
    return {
        "overall": evaluate_onnx_records(backend, records),
        "by_source": {
            source: evaluate_onnx_records(backend, source_records)
            for source, source_records in sorted(grouped.items())
        },
    }
