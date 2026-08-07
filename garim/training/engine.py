"""Device-agnostic joint training loop; callers provide already tokenized DataLoaders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch

from garim.training.losses import JointLoss, compute_joint_loss
from garim.training.model import JointL1Model


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    learning_rate: float = 3e-5
    weight_decay: float = 0.01
    gradient_clip: float = 1.0
    task_lambda: float = 1.0
    hard_label_alpha: float = 0.5
    temperature: float = 2.0
    qwen_weight: float = 0.25
    auxiliary_weight: float = 0.25
    pii_o_weight: float = 0.25


@dataclass(frozen=True, slots=True)
class EpochLoss:
    total: float
    pii: float
    attack_ce: float
    kanana_kl: float
    qwen_jailbreak: float
    romanized: float
    honorific: float
    steps: int


def build_optimizer(model: JointL1Model, config: TrainingConfig) -> torch.optim.Optimizer:
    decay: list[torch.nn.Parameter] = []
    no_decay: list[torch.nn.Parameter] = []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        destination = no_decay if name.endswith(("bias", "LayerNorm.weight")) else decay
        destination.append(parameter)
    return torch.optim.AdamW(
        [
            {"params": decay, "weight_decay": config.weight_decay},
            {"params": no_decay, "weight_decay": 0.0},
        ],
        lr=config.learning_rate,
    )


def _device_value(value: Any, device: torch.device) -> Any:
    return value.to(device) if isinstance(value, torch.Tensor) else value


def train_epoch(
    model: JointL1Model,
    batches: Any,
    optimizer: torch.optim.Optimizer,
    *,
    device: torch.device,
    config: TrainingConfig,
    scheduler: Any | None = None,
) -> EpochLoss:
    """Train one epoch. `normalization_delta` must already be ordered/log1p encoded."""
    model.train()
    totals = {
        "total": 0.0,
        "pii": 0.0,
        "attack_ce": 0.0,
        "kanana_kl": 0.0,
        "qwen_jailbreak": 0.0,
        "romanized": 0.0,
        "honorific": 0.0,
    }
    steps = 0
    for raw_batch in batches:
        batch = {key: _device_value(value, device) for key, value in raw_batch.items()}
        optimizer.zero_grad(set_to_none=True)
        output = model(
            batch["input_ids"],
            batch["attention_mask"],
            batch["normalization_delta"],
            batch.get("token_type_ids"),
        )
        loss: JointLoss = compute_joint_loss(
            output,
            pii_labels=batch.get("pii_labels"),
            attack_labels=batch.get("attack_labels"),
            kanana_probabilities=batch.get("kanana_probabilities"),
            kanana_mask=batch.get("kanana_mask"),
            qwen_jailbreak_targets=batch.get("qwen_jailbreak_targets"),
            romanized_targets=batch.get("romanized_targets"),
            honorific_targets=batch.get("honorific_targets"),
            task_lambda=config.task_lambda,
            hard_label_alpha=config.hard_label_alpha,
            temperature=config.temperature,
            qwen_weight=config.qwen_weight,
            auxiliary_weight=config.auxiliary_weight,
            pii_o_weight=config.pii_o_weight,
        )
        loss.total.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip)
        optimizer.step()
        if scheduler is not None:
            scheduler.step()
        for key in totals:
            totals[key] += float(getattr(loss, key).detach().cpu())
        steps += 1
    if steps == 0:
        raise ValueError("training DataLoader yielded no batches")
    return EpochLoss(steps=steps, **{key: value / steps for key, value in totals.items()})
