"""Device-agnostic joint training loop; callers provide already tokenized DataLoaders."""

from __future__ import annotations

import contextlib
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
    # ★ 24GB 카드(RTX 3090/4090)에서 batch 32 × 512토큰 fp32 가 들어간다는 근거가 없었다.
    #   논리 배치(옵티마이저 한 스텝 = 한 태스크의 batch_size 행)는 그대로 두고, 메모리만
    #   줄이는 두 손잡이다. 청크마다 기울기를 쌓고 한 번 스텝하므로 학습 의미는 같다.
    micro_batch_size: int | None = None
    # "bf16" 은 CUDA 에서만 autocast 를 켠다(master weight 는 fp32 그대로). 다른 장치는 fp32.
    precision: str = "fp32"


def uses_bf16(config: TrainingConfig, device: torch.device) -> bool:
    if config.precision not in {"fp32", "bf16"}:
        raise ValueError(f"precision must be fp32 or bf16, got {config.precision!r}")
    return config.precision == "bf16" and device.type == "cuda"


def _split_batch(batch: dict[str, Any], size: int | None) -> list[dict[str, Any]]:
    """논리 배치를 행 단위 청크로 나눈다. 텐서와 행 단위 리스트를 같은 자리에서 자른다."""
    rows = int(batch["input_ids"].shape[0])
    if size is None or size >= rows:
        return [batch]
    return [
        {
            key: value[start : start + size] if isinstance(value, torch.Tensor | list) else value
            for key, value in batch.items()
        }
        for start in range(0, rows, size)
    ]


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
    bf16 = uses_bf16(config, device)
    for raw_batch in batches:
        rows = int(raw_batch["input_ids"].shape[0])
        optimizer.zero_grad(set_to_none=True)
        for chunk in _split_batch(raw_batch, config.micro_batch_size):
            batch = {key: _device_value(value, device) for key, value in chunk.items()}
            # 청크 손실을 행 비율로 가중한다 — 청크 평균의 가중합이 배치 평균과 통계적으로
            # 같다(토큰 수가 청크마다 다를 때 비트 동일은 아니다).
            weight = int(batch["input_ids"].shape[0]) / rows
            autocast = (
                torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                if bf16
                else contextlib.nullcontext()
            )
            with autocast:
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
            (loss.total * weight).backward()
            for key in totals:
                totals[key] += float(getattr(loss, key).detach().float().cpu()) * weight
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip)
        optimizer.step()
        if scheduler is not None:
            scheduler.step()
        steps += 1
    if steps == 0:
        raise ValueError("training DataLoader yielded no batches")
    return EpochLoss(steps=steps, **{key: value / steps for key, value in totals.items()})
