"""Shared-encoder/four-head L1 model used for offline training and ONNX export."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Literal

import torch
from torch import nn

Pooling = Literal["mean", "cls"]


@dataclass(frozen=True, slots=True)
class JointL1Output:
    pii_logits: torch.Tensor
    attack_logits: torch.Tensor
    romanized_logits: torch.Tensor
    honorific_logits: torch.Tensor

    def as_dict(self) -> dict[str, torch.Tensor]:
        return {
            "pii_logits": self.pii_logits,
            "attack_logits": self.attack_logits,
            "romanized_logits": self.romanized_logits,
            "honorific_logits": self.honorific_logits,
        }


class JointL1Model(nn.Module):
    """One encoder forward with a token head and three sequence heads.

    Normalization deltas belong to sequence-level attack/evasion decisions. They are not
    broadcast into the PII head, so the token head remains `[batch, tokens, 29]` and cannot
    learn shortcuts from document-level mutation counts.
    """

    def __init__(
        self,
        encoder: nn.Module,
        *,
        hidden_size: int,
        pii_labels: int = 29,
        attack_labels: int = 4,
        normalization_features: int = 8,
        dropout: float = 0.1,
        pooling: Pooling = "mean",
    ) -> None:
        super().__init__()
        if pooling not in {"mean", "cls"}:
            raise ValueError("pooling must be 'mean' or 'cls'")
        self.encoder = encoder
        try:
            parameters = inspect.signature(encoder.forward).parameters.values()
            self._encoder_accepts_token_type_ids = any(
                parameter.name == "token_type_ids"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            # token_type_ids is optional tokenizer output. If an opaque/scripted encoder
            # cannot expose its signature, omitting it is the only backbone-safe choice.
            self._encoder_accepts_token_type_ids = False
        self.pooling = pooling
        self.normalization_features = normalization_features
        self.dropout = nn.Dropout(dropout)
        self.pii_head = nn.Linear(hidden_size, pii_labels)
        sequence_size = hidden_size + normalization_features
        self.attack_head = nn.Linear(sequence_size, attack_labels)
        self.romanized_head = nn.Linear(sequence_size, 1)
        self.honorific_head = nn.Linear(sequence_size, 1)

    @classmethod
    def from_pretrained(
        cls,
        model_name_or_path: str,
        *,
        local_files_only: bool = True,
        pooling: Pooling = "mean",
        **model_kwargs: Any,
    ) -> JointL1Model:
        """Build from a local encoder by default; callers must opt in to downloads."""
        from transformers import AutoModel

        encoder = AutoModel.from_pretrained(
            model_name_or_path,
            local_files_only=local_files_only,
            **model_kwargs,
        )
        hidden_size = int(encoder.config.hidden_size)
        return cls(encoder, hidden_size=hidden_size, pooling=pooling)

    def _pool(self, hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        if self.pooling == "cls":
            return hidden[:, 0]
        weights = attention_mask.to(dtype=hidden.dtype).unsqueeze(-1)
        denominator = weights.sum(dim=1).clamp_min(1.0)
        return (hidden * weights).sum(dim=1) / denominator

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        normalization_delta: torch.Tensor,
        token_type_ids: torch.Tensor | None = None,
    ) -> JointL1Output:
        if normalization_delta.ndim != 2 or normalization_delta.shape[-1] != (
            self.normalization_features
        ):
            raise ValueError(
                f"normalization_delta must have shape [batch, {self.normalization_features}]"
            )
        encoder_inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if token_type_ids is not None and self._encoder_accepts_token_type_ids:
            encoder_inputs["token_type_ids"] = token_type_ids
        encoded = self.encoder(**encoder_inputs)
        hidden = encoded.last_hidden_state
        pii_logits = self.pii_head(self.dropout(hidden))
        pooled = self._pool(hidden, attention_mask)
        sequence_features = torch.cat(
            (self.dropout(pooled), normalization_delta.to(dtype=hidden.dtype)), dim=-1
        )
        return JointL1Output(
            pii_logits=pii_logits,
            attack_logits=self.attack_head(sequence_features),
            romanized_logits=self.romanized_head(sequence_features),
            honorific_logits=self.honorific_head(sequence_features),
        )
