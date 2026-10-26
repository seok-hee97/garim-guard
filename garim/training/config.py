"""Pinned joint-training hyperparameter manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seeds: list[int] = Field(min_length=3)
    epochs: int = Field(5, ge=1)
    batch_size: int = Field(32, ge=1)
    max_length: int = Field(512, ge=8)
    stride: int = Field(64, ge=0)
    pii_per_attack: int = Field(4, ge=1)
    learning_rate: float = Field(3e-5, gt=0)
    weight_decay: float = Field(0.01, ge=0)
    gradient_clip: float = Field(1.0, gt=0)
    warmup_ratio: float = Field(0.1, ge=0, lt=1)
    task_lambda: float = Field(1.0, gt=0)
    hard_label_alpha: float = Field(0.5, ge=0, le=1)
    temperature: float = Field(2.0, gt=0)
    qwen_weight: float = Field(0.25, ge=0)
    auxiliary_weight: float = Field(0.25, ge=0)
    pii_o_weight: float = Field(0.25, gt=0)
    early_stopping_patience: int = Field(2, ge=1)
    # 메모리 손잡이 — `garim/training/engine.py` 참고. 논리 배치는 `batch_size` 그대로다.
    micro_batch_size: int | None = Field(None, ge=1)
    precision: Literal["fp32", "bf16"] = "fp32"

    @model_validator(mode="after")
    def validate_config(self) -> RunConfig:
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("training seeds must be unique")
        if self.stride >= self.max_length - 2:
            raise ValueError("stride must leave forward progress inside max_length")
        if self.micro_batch_size is not None and self.micro_batch_size > self.batch_size:
            raise ValueError("micro_batch_size cannot exceed batch_size")
        return self


def load_run_config(path: str | Path = "train/training.yaml") -> RunConfig:
    config_path = Path(path)
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load training config {config_path}: {exc}") from exc
    return RunConfig.model_validate(payload)
