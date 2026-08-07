"""Joint-model validation metrics used for early stopping and final reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from garim.training.export import PII_LABELS
from garim.training.teachers import ATTACK_LABELS


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    pii_character_precision: float
    pii_character_recall: float
    pii_character_f1: float
    attack_macro_precision: float
    attack_macro_recall: float
    attack_macro_f1: float
    attack_false_positive_rate: float
    examples: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evaluate_joint_model(
    model: Any,
    dataset: Any,
    collator: Any,
    *,
    device: Any,
    batch_size: int = 32,
) -> EvaluationMetrics:
    import torch
    from torch.utils.data import DataLoader

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, collate_fn=collator)
    gold_chars: set[tuple[str, int, str]] = set()
    predicted_chars: set[tuple[str, int, str]] = set()
    attack_logits: dict[str, list[torch.Tensor]] = defaultdict(list)
    attack_gold: dict[str, int] = {}

    model.eval()
    with torch.inference_mode():
        for batch in loader:
            output = model(
                batch["input_ids"].to(device),
                batch["attention_mask"].to(device),
                batch["normalization_delta"].to(device),
                batch.get("token_type_ids").to(device)
                if batch.get("token_type_ids") is not None
                else None,
            )
            predicted_ids = output.pii_logits.argmax(dim=-1).cpu()
            # Metrics read the eval tensor, not the loss tensor: attack rows keep their
            # masked PII loss but still expose false positives to precision.
            gold_ids = batch["pii_eval_labels"]
            for row, record_id in enumerate(batch["record_ids"]):
                for token_index, (start, end) in enumerate(batch["offset_mappings"][row]):
                    gold_id = int(gold_ids[row, token_index])
                    if gold_id == -100 or start == end:
                        continue
                    predicted_id = int(predicted_ids[row, token_index])
                    gold_label = PII_LABELS[gold_id]
                    predicted_label = PII_LABELS[predicted_id]
                    for position in range(start, end):
                        if gold_label != "O":
                            gold_chars.add((record_id, position, gold_label[2:]))
                        if predicted_label != "O":
                            predicted_chars.add((record_id, position, predicted_label[2:]))

                label = int(batch["attack_labels"][row])
                if label != -100:
                    previous = attack_gold.setdefault(record_id, label)
                    if previous != label:
                        raise ValueError(f"attack label changes across windows: {record_id}")
                    attack_logits[record_id].append(output.attack_logits[row].detach().cpu())

    character_tp = len(gold_chars & predicted_chars)
    pii_precision = _safe_div(character_tp, len(predicted_chars))
    pii_recall = _safe_div(character_tp, len(gold_chars))

    confusion = [[0 for _ in ATTACK_LABELS] for _ in ATTACK_LABELS]
    for record_id, gold in attack_gold.items():
        prediction = int(torch.stack(attack_logits[record_id]).mean(dim=0).argmax())
        confusion[gold][prediction] += 1

    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    for label in range(len(ATTACK_LABELS)):
        true_positive = confusion[label][label]
        predicted = sum(row[label] for row in confusion)
        actual = sum(confusion[label])
        precision = _safe_div(true_positive, predicted)
        recall = _safe_div(true_positive, actual)
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(_f1(precision, recall))
    none_total = sum(confusion[0])
    none_false_positive = none_total - confusion[0][0]
    return EvaluationMetrics(
        pii_character_precision=pii_precision,
        pii_character_recall=pii_recall,
        pii_character_f1=_f1(pii_precision, pii_recall),
        attack_macro_precision=sum(precisions) / len(precisions),
        attack_macro_recall=sum(recalls) / len(recalls),
        attack_macro_f1=sum(f1s) / len(f1s),
        attack_false_positive_rate=_safe_div(none_false_positive, none_total),
        examples=len(set(attack_gold) | {item[0] for item in gold_chars}),
    )
