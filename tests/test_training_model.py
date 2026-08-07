from __future__ import annotations

from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torch.nn")

from garim.training.losses import compute_joint_loss  # noqa: E402
from garim.training.model import JointL1Model  # noqa: E402


class FakeEncoder(torch.nn.Module):
    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(32, hidden_size)

    def forward(self, input_ids, attention_mask, token_type_ids=None):  # noqa: ANN001
        del attention_mask, token_type_ids
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


def test_joint_model_exposes_runtime_onnx_contract() -> None:
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    output = model(
        input_ids=torch.randint(0, 32, (2, 5)),
        attention_mask=torch.ones((2, 5), dtype=torch.long),
        normalization_delta=torch.zeros((2, 8)),
    )
    assert output.pii_logits.shape == (2, 5, 29)
    assert output.attack_logits.shape == (2, 4)
    assert output.romanized_logits.shape == (2, 1)
    assert output.honorific_logits.shape == (2, 1)


def test_joint_model_drops_token_type_ids_for_ax_style_encoder() -> None:
    class AxStyleEncoder(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embedding = torch.nn.Embedding(32, 12)

        def forward(self, input_ids, attention_mask):  # noqa: ANN001
            del attention_mask
            return SimpleNamespace(last_hidden_state=self.embedding(input_ids))

    model = JointL1Model(AxStyleEncoder(), hidden_size=12, dropout=0.0)
    output = model(
        input_ids=torch.randint(0, 32, (2, 5)),
        attention_mask=torch.ones((2, 5), dtype=torch.long),
        normalization_delta=torch.zeros((2, 8)),
        token_type_ids=torch.zeros((2, 5), dtype=torch.long),
    )
    assert output.pii_logits.shape == (2, 5, 29)


def test_joint_loss_uses_binary_auxiliary_losses_and_restricted_kl() -> None:
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    output = model(
        input_ids=torch.randint(0, 32, (2, 5)),
        attention_mask=torch.ones((2, 5), dtype=torch.long),
        normalization_delta=torch.zeros((2, 8)),
    )
    loss = compute_joint_loss(
        output,
        pii_labels=torch.zeros((2, 5), dtype=torch.long),
        attack_labels=torch.tensor([1, 2]),
        kanana_probabilities=torch.tensor([[0.1, 0.8, 0.1], [0.8, 0.1, 0.1]]),
        kanana_mask=torch.tensor([True, False]),
        qwen_jailbreak_targets=torch.tensor([0.0, 1.0]),
        romanized_targets=torch.tensor([1.0, -1.0]),
        honorific_targets=torch.tensor([0.0, 1.0]),
    )
    assert torch.isfinite(loss.total)
    loss.total.backward()
    assert model.attack_head.weight.grad is not None
