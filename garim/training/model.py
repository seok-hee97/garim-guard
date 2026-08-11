"""Shared-encoder/four-head L1 model used for offline training and ONNX export."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Literal

import torch
from torch import nn

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER

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
        normalization_features: int = len(NORMALIZATION_DELTA_ORDER),
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
        # ★ 구성 시점에 막는다. 인코더와 헤드의 dtype 이 갈리면 첫 forward 의 matmul 에서
        #   죽는데, 그때는 데이터 로딩·토크나이즈를 다 지나온 뒤라 원인이 멀어 보인다.
        #   MPS 에서는 Metal 어서션으로 나와 기기 문제처럼 보이기까지 한다.
        encoder_dtype = next(encoder.parameters(), None)
        if encoder_dtype is not None and encoder_dtype.dtype != self.pii_head.weight.dtype:
            raise ValueError(
                f"encoder dtype {encoder_dtype.dtype} does not match head dtype "
                f"{self.pii_head.weight.dtype} — pass dtype= explicitly when loading "
                f"the encoder (checkpoints may declare bfloat16 and transformers 5.x "
                f"honours it by default)"
            )

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

        # ★ dtype 을 명시한다. 채택 백본 `skt/A.X-Encoder-base` 의 config 가
        #   `"torch_dtype": "bfloat16"` 이고 transformers 5.x 는 그것을 **기본으로
        #   존중한다.** 그러면 인코더는 bf16, 아래에서 새로 만드는 헤드는 fp32 가 되어
        #   첫 forward 에서 죽는다.
        #
        #       RuntimeError: mat1 and mat2 must have the same dtype,
        #           but got BFloat16 and Float
        #
        #   MPS 에서는 같은 원인이 Metal 어서션으로 먼저 나오는데(`Destination NDArray
        #   and Accumulator NDArray cannot have different datatype`) 기기 문제가 아니다.
        #   CUDA 도 같은 규칙이라 RunPod 첫 스텝에서 똑같이 터진다.
        #
        #   학습 경로이므로 **master weight 를 fp32 로** 잡는다. 혼합 정밀도는 저장
        #   dtype 이 조용히 정하는 것이 아니라 autocast 로 명시해서 켜는 것이 맞고,
        #   149M 인코더에서 fp32 는 600MB 라 4090 에 부담이 아니다. 호출자가 `dtype` 을
        #   직접 주면 그 선택을 존중한다.
        encoder = AutoModel.from_pretrained(
            model_name_or_path,
            local_files_only=local_files_only,
            **{"dtype": torch.float32, **model_kwargs},
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
