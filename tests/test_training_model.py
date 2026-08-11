from __future__ import annotations

from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torch.nn")

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER  # noqa: E402
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
        normalization_delta=torch.zeros((2, len(NORMALIZATION_DELTA_ORDER))),
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
        normalization_delta=torch.zeros((2, len(NORMALIZATION_DELTA_ORDER))),
        token_type_ids=torch.zeros((2, 5), dtype=torch.long),
    )
    assert output.pii_logits.shape == (2, 5, 29)


def test_joint_loss_uses_binary_auxiliary_losses_and_restricted_kl() -> None:
    model = JointL1Model(FakeEncoder(12), hidden_size=12, dropout=0.0)
    output = model(
        input_ids=torch.randint(0, 32, (2, 5)),
        attention_mask=torch.ones((2, 5), dtype=torch.long),
        normalization_delta=torch.zeros((2, len(NORMALIZATION_DELTA_ORDER))),
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


def test_encoder_and_heads_share_one_dtype() -> None:
    """★ 채택 백본으로 학습이 첫 forward 에서 죽었다.

    `skt/A.X-Encoder-base` 의 config 가 `"torch_dtype": "bfloat16"` 이고 transformers
    5.x 는 그것을 **기본으로 존중한다.** 그러면 인코더 bf16 / 헤드 fp32 가 되어

        RuntimeError: mat1 and mat2 must have the same dtype, but got BFloat16 and Float

    가 난다. MPS 에서는 같은 원인이 Metal 어서션으로 먼저 나와 기기 문제처럼 보이지만
    CUDA 도 같은 규칙이라 RunPod 첫 스텝에서 똑같이 터진다.
    """
    model = JointL1Model(FakeEncoder(12), hidden_size=12)
    assert next(model.encoder.parameters()).dtype == model.pii_head.weight.dtype


def test_a_dtype_divergence_is_refused_at_construction() -> None:
    """첫 matmul 까지 기다리면 데이터 로딩을 다 지나온 뒤라 원인이 멀어 보인다."""
    encoder = FakeEncoder(12).to(dtype=torch.bfloat16)
    with pytest.raises(ValueError, match="does not match head dtype"):
        JointL1Model(encoder, hidden_size=12)
