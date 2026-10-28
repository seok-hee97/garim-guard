"""Masked multi-task and class-restricted distillation losses for JointL1Model."""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as functional

from garim.training.model import JointL1Output

_KANANA_STUDENT_CLASSES = (0, 1, 3)  # NONE / INJECTION / SYSTEM_LEAK


@dataclass(frozen=True, slots=True)
class JointLoss:
    total: torch.Tensor
    pii: torch.Tensor
    attack_ce: torch.Tensor
    kanana_kl: torch.Tensor
    qwen_jailbreak: torch.Tensor
    romanized: torch.Tensor
    honorific: torch.Tensor


def _zero(output: JointL1Output) -> torch.Tensor:
    return output.attack_logits.sum() * 0.0


def _masked_binary_loss(
    logits: torch.Tensor,
    targets: torch.Tensor | None,
    *,
    missing_value: float = -1.0,
) -> torch.Tensor:
    if targets is None:
        return logits.sum() * 0.0
    targets = targets.to(device=logits.device, dtype=logits.dtype).reshape_as(logits)
    mask = targets != missing_value
    if not bool(mask.any()):
        return logits.sum() * 0.0
    return functional.binary_cross_entropy_with_logits(logits[mask], targets[mask])


def compute_joint_loss(
    output: JointL1Output,
    *,
    pii_labels: torch.Tensor | None = None,
    attack_labels: torch.Tensor | None = None,
    kanana_probabilities: torch.Tensor | None = None,
    kanana_mask: torch.Tensor | None = None,
    qwen_jailbreak_targets: torch.Tensor | None = None,
    romanized_targets: torch.Tensor | None = None,
    honorific_targets: torch.Tensor | None = None,
    task_lambda: float = 1.0,
    hard_label_alpha: float = 0.5,
    temperature: float = 2.0,
    qwen_weight: float = 0.25,
    auxiliary_weight: float = 0.25,
    pii_o_weight: float = 0.25,
) -> JointLoss:
    """Compute only losses whose labels are present; missing labels use sentinel values.

    `attack_labels` uses -100 for missing labels. Binary targets use -1. Kanana probabilities
    must be ordered NONE/INJECTION/SYSTEM_LEAK and never invent a JAILBREAK probability.
    """
    zero = _zero(output)
    if pii_labels is None or not bool((pii_labels != -100).any()):
        pii = zero
    else:
        if pii_o_weight <= 0:
            raise ValueError("pii_o_weight must be positive")
        pii_weights = torch.ones(
            output.pii_logits.shape[-1],
            device=output.pii_logits.device,
            dtype=output.pii_logits.dtype,
        )
        pii_weights[0] = pii_o_weight
        pii = functional.cross_entropy(
            output.pii_logits.reshape(-1, output.pii_logits.shape[-1]),
            pii_labels.to(device=output.pii_logits.device).reshape(-1),
            ignore_index=-100,
            weight=pii_weights,
        )

    if attack_labels is None or not bool((attack_labels != -100).any()):
        attack_ce = zero
    else:
        attack_ce = functional.cross_entropy(
            output.attack_logits,
            attack_labels.to(device=output.attack_logits.device),
            ignore_index=-100,
        )

    kanana_kl = zero
    if kanana_probabilities is not None:
        probabilities = kanana_probabilities.to(
            device=output.attack_logits.device, dtype=output.attack_logits.dtype
        )
        if probabilities.ndim != 2 or probabilities.shape[-1] != 3:
            raise ValueError("kanana_probabilities must have shape [batch, 3]")
        mask = (
            torch.ones(probabilities.shape[0], dtype=torch.bool, device=probabilities.device)
            if kanana_mask is None
            else kanana_mask.to(device=probabilities.device, dtype=torch.bool)
        )
        if bool(mask.any()):
            selected = output.attack_logits[:, _KANANA_STUDENT_CLASSES]
            student_log = functional.log_softmax(selected[mask] / temperature, dim=-1)
            teacher = probabilities[mask]
            teacher = teacher / teacher.sum(dim=-1, keepdim=True).clamp_min(1e-8)
            kanana_kl = functional.kl_div(student_log, teacher, reduction="batchmean")
            kanana_kl = kanana_kl * temperature**2

    qwen_jailbreak = _masked_binary_loss(output.attack_logits[:, 2], qwen_jailbreak_targets)
    romanized = _masked_binary_loss(output.romanized_logits[:, 0], romanized_targets)
    honorific = _masked_binary_loss(output.honorific_logits[:, 0], honorific_targets)

    attack = (
        hard_label_alpha * attack_ce
        + (1.0 - hard_label_alpha) * kanana_kl
        + qwen_weight * qwen_jailbreak
        + auxiliary_weight * (romanized + honorific)
    )
    total = pii + task_lambda * attack
    return JointLoss(
        total=total,
        pii=pii,
        attack_ce=attack_ce,
        kanana_kl=kanana_kl,
        qwen_jailbreak=qwen_jailbreak,
        romanized=romanized,
        honorific=honorific,
    )
