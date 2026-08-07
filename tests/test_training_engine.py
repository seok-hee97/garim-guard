"""The optimizer loop itself — previously this file only exercised early stopping."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from garim.training.engine import (  # noqa: E402
    TrainingConfig,
    build_optimizer,
    train_epoch,
)
from garim.training.model import JointL1Model  # noqa: E402


class FakeEncoder(torch.nn.Module):
    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(32, hidden_size)

    def forward(self, input_ids, attention_mask, token_type_ids=None):  # noqa: ANN001
        del attention_mask, token_type_ids
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


def _batch(*, task: str) -> dict[str, torch.Tensor]:
    batch = {
        "input_ids": torch.randint(0, 32, (2, 6)),
        "attention_mask": torch.ones((2, 6), dtype=torch.long),
        "normalization_delta": torch.zeros((2, 8)),
        "romanized_targets": torch.tensor([-1.0, -1.0]),
        "honorific_targets": torch.tensor([-1.0, -1.0]),
    }
    if task == "pii":
        batch["pii_labels"] = torch.zeros((2, 6), dtype=torch.long)
        batch["attack_labels"] = torch.full((2,), -100, dtype=torch.long)
    else:
        batch["pii_labels"] = torch.full((2, 6), -100, dtype=torch.long)
        batch["attack_labels"] = torch.tensor([1, 2])
        batch["kanana_probabilities"] = torch.tensor([[0.2, 0.7, 0.1], [0.6, 0.3, 0.1]])
        batch["kanana_mask"] = torch.tensor([True, True])
        batch["qwen_jailbreak_targets"] = torch.tensor([0.0, 1.0])
    return batch


def test_build_optimizer_excludes_bias_and_layernorm_from_weight_decay() -> None:
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    optimizer = build_optimizer(model, TrainingConfig())
    decay, no_decay = optimizer.param_groups
    assert decay["weight_decay"] == pytest.approx(0.01)
    assert no_decay["weight_decay"] == 0.0
    assert no_decay["params"], "bias parameters must land in the no-decay group"
    assert len(decay["params"]) + len(no_decay["params"]) == len(
        list(model.parameters())
    )


def test_train_epoch_steps_the_optimizer_on_interleaved_tasks() -> None:
    torch.manual_seed(0)
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    config = TrainingConfig(learning_rate=1e-2)
    optimizer = build_optimizer(model, config)
    before = model.attack_head.weight.detach().clone()

    losses = train_epoch(
        model,
        [_batch(task="pii"), _batch(task="attack"), _batch(task="pii")],
        optimizer,
        device=torch.device("cpu"),
        config=config,
    )

    assert losses.steps == 3
    assert losses.total > 0.0
    # Attack rows mask the PII loss and vice versa, so both must be non-zero across
    # an interleaved epoch — a silently masked task would leave one of these at zero.
    assert losses.pii > 0.0
    assert losses.attack_ce > 0.0
    assert losses.kanana_kl > 0.0
    assert not torch.allclose(before, model.attack_head.weight)


def test_train_epoch_rejects_an_empty_loader() -> None:
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    config = TrainingConfig()
    with pytest.raises(ValueError, match="no batches"):
        train_epoch(
            model,
            [],
            build_optimizer(model, config),
            device=torch.device("cpu"),
            config=config,
        )
