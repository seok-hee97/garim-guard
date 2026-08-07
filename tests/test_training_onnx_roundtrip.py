"""Torch model -> ONNX -> QInt8 -> the runtime's own backend, with no pinned assets.

This is the seam that had never executed. `export_joint_onnx`, `quantize_joint_onnx` and
`OnnxL1Backend` were each "구현 완료" on the strength of a test suite that skipped every
torch test, so the first real run would have been on the GPU box. A tiny encoder and a
hand-built fast tokenizer are enough to hold the contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("onnxruntime")
pytest.importorskip("transformers")

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER, OnnxL1Backend  # noqa: E402
from garim.training.export import (  # noqa: E402
    PII_LABELS,
    export_joint_onnx,
    quantize_joint_onnx,
    write_l1_metadata,
)
from garim.training.model import JointL1Model  # noqa: E402

HIDDEN = 16
VOCAB = 64


class TinyEncoder(torch.nn.Module):
    """Stands in for A.X: same interface, small enough to export in a unit test."""

    def __init__(self) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(VOCAB, HIDDEN)
        self.projection = torch.nn.Linear(HIDDEN, HIDDEN)

    def forward(self, input_ids, attention_mask, token_type_ids=None):  # noqa: ANN001
        del attention_mask, token_type_ids
        hidden = self.projection(torch.tanh(self.embedding(input_ids)))
        return SimpleNamespace(last_hidden_state=hidden)


def _write_fast_tokenizer(directory: Path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    words = ["[UNK]", "[PAD]", "홍길동", "에게", "전달", "해", "주세요", "이전", "지시", "무시"]
    vocab = {word: index for index, word in enumerate(words)}
    backend = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    fast = PreTrainedTokenizerFast(
        tokenizer_object=backend, unk_token="[UNK]", pad_token="[PAD]"
    )
    fast.save_pretrained(directory)


@pytest.fixture(scope="module")
def l1_model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("l1")
    torch.manual_seed(11)
    model = JointL1Model(TinyEncoder(), hidden_size=HIDDEN, dropout=0.0)
    export_joint_onnx(
        model,
        directory / "garim_l1.fp32.onnx",
        input_ids=torch.randint(0, VOCAB, (1, 8)),
        attention_mask=torch.ones((1, 8), dtype=torch.long),
        normalization_delta=torch.zeros((1, len(NORMALIZATION_DELTA_ORDER))),
    )
    quantize_joint_onnx(
        directory / "garim_l1.fp32.onnx", directory / "garim_l1.int8.onnx"
    )
    write_l1_metadata(directory)
    _write_fast_tokenizer(directory)
    return directory


def test_exported_graph_exposes_the_runtime_input_and_output_contract(
    l1_model_dir: Path,
) -> None:
    import onnxruntime as ort

    session = ort.InferenceSession(
        str(l1_model_dir / "garim_l1.fp32.onnx"), providers=["CPUExecutionProvider"]
    )
    # The runtime raises if `normalization_delta` is absent (§6.2.2), so the exporter
    # has to put it in the graph — that pairing was never checked end to end.
    assert {item.name for item in session.get_inputs()} == {
        "input_ids",
        "attention_mask",
        "normalization_delta",
    }
    assert {item.name for item in session.get_outputs()} == {
        "pii_logits",
        "attack_logits",
        "romanized_logits",
        "honorific_logits",
    }


def test_quantized_artifact_loads_in_the_runtime_backend_and_infers(
    l1_model_dir: Path,
) -> None:
    backend = OnnxL1Backend(l1_model_dir)
    assert backend.pii_labels == PII_LABELS

    text = "홍길동 에게 전달 해 주세요"
    output = backend.infer(
        text,
        normalization_delta=dict.fromkeys(NORMALIZATION_DELTA_ORDER, 0),
        origin="user",
        content_type="prose",
    )
    output.validate(len(text))
    assert set(output.attack_probabilities) == {
        "NONE",
        "INJECTION",
        "JAILBREAK",
        "SYSTEM_LEAK",
    }
    assert sum(output.attack_probabilities.values()) == pytest.approx(1.0, abs=1e-4)
    assert all(0 <= e.span[0] < e.span[1] <= len(text) for e in output.entities)


def test_backend_rejects_a_graph_without_the_normalization_delta_input(
    tmp_path: Path, l1_model_dir: Path
) -> None:
    """A re-export that drops the delta head must fail loudly, not skip the feature."""

    class DeltaFreeWrapper(torch.nn.Module):
        """What an export looks like when §6.2.2's delta feature is not wired in-graph."""

        def __init__(self, joint: JointL1Model) -> None:
            super().__init__()
            self.joint = joint

        def forward(self, input_ids, attention_mask):  # noqa: ANN001
            zeros = torch.zeros(
                (input_ids.shape[0], len(NORMALIZATION_DELTA_ORDER)),
                dtype=torch.float32,
            )
            out = self.joint(input_ids, attention_mask, zeros)
            return (
                out.pii_logits,
                out.attack_logits,
                out.romanized_logits,
                out.honorific_logits,
            )

    directory = tmp_path / "no-delta"
    directory.mkdir()
    for item in l1_model_dir.iterdir():
        if item.is_file() and item.suffix != ".onnx":
            (directory / item.name).write_bytes(item.read_bytes())

    torch.manual_seed(11)
    wrapper = DeltaFreeWrapper(JointL1Model(TinyEncoder(), hidden_size=HIDDEN, dropout=0.0))
    wrapper.eval()
    dynamic = torch.export.Dim.DYNAMIC
    torch.onnx.export(
        wrapper,
        (torch.randint(0, VOCAB, (1, 8)), torch.ones((1, 8), dtype=torch.long)),
        directory / "garim_l1.int8.onnx",
        input_names=["input_ids", "attention_mask"],
        output_names=[
            "pii_logits",
            "attack_logits",
            "romanized_logits",
            "honorific_logits",
        ],
        dynamic_shapes={
            "input_ids": {0: dynamic, 1: dynamic},
            "attention_mask": {0: dynamic, 1: dynamic},
        },
        opset_version=18,
        dynamo=True,
    )

    backend = OnnxL1Backend(directory)
    with pytest.raises(RuntimeError, match="normalization_delta"):
        backend.infer(
            "홍길동",
            normalization_delta=dict.fromkeys(NORMALIZATION_DELTA_ORDER, 0),
            origin="user",
            content_type="prose",
        )


def test_entity_decoder_merges_bio_runs_and_splits_on_new_begin(
    l1_model_dir: Path,
) -> None:
    """Drive the decoder with fixed logits; random weights cannot pin down a label."""
    import numpy as np

    backend = OnnxL1Backend(l1_model_dir)
    offsets = [(0, 0), (0, 3), (3, 5), (5, 8), (8, 9), (0, 0)]
    logits = np.full((1, len(offsets), len(PII_LABELS)), -8.0, dtype=np.float32)

    def pick(token: int, label: str) -> None:
        logits[0, token, PII_LABELS.index(label)] = 8.0

    pick(0, "O")
    pick(1, "B-PERSON")
    pick(2, "I-PERSON")
    pick(3, "B-PERSON")  # a fresh B must start a second entity, not extend the first
    pick(4, "O")
    pick(5, "O")

    entities = backend._decode_entities(logits, offsets)
    assert [(e.label, e.span) for e in entities] == [
        ("PERSON", (0, 5)),
        ("PERSON", (5, 8)),
    ]
