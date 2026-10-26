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


def test_cli_check_runs_l1_from_the_model_dir(l1_model_dir: Path, capsys) -> None:  # noqa: ANN001
    """학습 산출물이 생겨도 CLI 에 꽂을 자리가 없었다 — `--l1-model-dir` 의 왕복."""
    import json

    from garim.cli import main

    text = "홍길동 에게 전달 해 주세요"
    code = main(["--l1-model-dir", str(l1_model_dir), "check", "--json", text])
    payload = json.loads(capsys.readouterr().out)
    assert code != 4
    assert not payload["degraded"], payload["degraded_reasons"]
    assert payload["latency_ms"]["l1"] > 0


def test_proxy_loads_the_l1_backend_once_and_shares_it(l1_model_dir: Path) -> None:
    from garim.proxy import ProxyConfig, ProxyService

    def sender(path, payload, headers):  # noqa: ANN001, ANN202
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    service = ProxyService(
        ProxyConfig(upstream_base_url="https://upstream.test/v1", l1_model_dir=str(l1_model_dir)),
        sender=sender,
    )
    first = service._inspector_factory()
    second = service._inspector_factory()
    assert first.pipeline.l1_backend is second.pipeline.l1_backend
    assert first.vault is not second.vault  # 볼트는 요청마다 새것이다
    status, _, headers = service.handle_chat_completions(
        {"model": "x", "messages": [{"role": "user", "content": "홍길동 에게 전달 해 주세요"}]}, {}
    )
    assert status in {200, 400}
    assert headers["X-Garim-Degraded"] == "false"


def test_shared_backend_survives_concurrent_requests(l1_model_dir: Path) -> None:
    """chunk_text(무절단)와 infer(절단)가 토크나이저 상태를 번갈아 바꾼다 — 동시에 불러도 버틴다."""
    from concurrent.futures import ThreadPoolExecutor

    from garim import Inspector
    from garim.policy.schema import Limits, PolicyConfig

    backend = OnnxL1Backend(l1_model_dir)
    # 이 테스트가 보는 것은 충돌(`Already borrowed`)이지 지연이 아니다. 부하가 걸린 머신에서
    # 1초 예산을 넘는 것은 정상적인 `l1_timeout` 이라 예산을 넉넉히 둔다.
    policy = PolicyConfig(limits=Limits(l1_timeout_ms=60_000))

    def run(index: int) -> list[str]:
        text = "홍길동 에게 전달 해 주세요 " * (1 + index % 3)
        return Inspector(policy, l1_backend=backend).check(text).degraded_reasons

    with ThreadPoolExecutor(max_workers=8) as pool:
        reasons = [reason for found in pool.map(run, range(400)) for reason in found]
    assert not reasons, sorted(set(reasons))


class RangeEncoder(torch.nn.Module):
    """ModernBERT 처럼 시퀀스 길이에서 위치·국소 창을 만든다 — ONNX 에 동적 `Range` 가 생긴다."""

    def __init__(self) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(VOCAB, HIDDEN)
        self.position = torch.nn.Embedding(512, HIDDEN)

    def forward(self, input_ids, attention_mask, token_type_ids=None):  # noqa: ANN001
        del attention_mask, token_type_ids
        positions = torch.arange(input_ids.shape[1], device=input_ids.device)
        window = ((positions[None, :] - positions[:, None]).abs() <= 4).float()
        hidden = self.embedding(input_ids) + self.position(positions)[None]
        hidden = torch.einsum("ij,bjd->bid", window / window.sum(-1, keepdim=True), hidden)
        return SimpleNamespace(last_hidden_state=hidden)


def test_quantization_survives_a_dynamic_range_in_the_graph(tmp_path: Path) -> None:
    """★ 회귀 — 실제 A.X 그래프의 양자화가 symbolic shape inference 의 `_infer_Range` 에서 죽었다.

    `TinyEncoder` 에는 `Range` 가 없어 위 왕복이 통과했다. 로컬 CPU 미니 학습으로 처음 찾았고,
    RunPod 에서는 학습을 다 마친 뒤 11단계에서 드러날 자리였다.
    """
    model = JointL1Model(RangeEncoder(), hidden_size=HIDDEN, dropout=0.0)
    export_joint_onnx(
        model,
        tmp_path / "garim_l1.fp32.onnx",
        input_ids=torch.randint(0, VOCAB, (1, 8)),
        attention_mask=torch.ones((1, 8), dtype=torch.long),
        normalization_delta=torch.zeros((1, len(NORMALIZATION_DELTA_ORDER))),
    )
    quantize_joint_onnx(tmp_path / "garim_l1.fp32.onnx", tmp_path / "garim_l1.int8.onnx")
    write_l1_metadata(tmp_path)
    _write_fast_tokenizer(tmp_path)
    text = "홍길동 에게 전달 해 주세요"
    output = OnnxL1Backend(tmp_path).infer(
        text,
        normalization_delta=dict.fromkeys(NORMALIZATION_DELTA_ORDER, 0),
        origin="user",
        content_type="prose",
    )
    output.validate(len(text))


def test_memory_probe_builds_full_length_batches_and_steps(tmp_path: Path) -> None:
    """0b 프로브가 실제 데이터셋·콜레이터 모양의 최악 배치로 학습 스텝을 끝까지 돈다(CPU)."""
    from transformers import AutoTokenizer

    from garim.training.config import RunConfig
    from garim.training.memory_probe import probe

    _write_fast_tokenizer(tmp_path)
    tokenizer = AutoTokenizer.from_pretrained(tmp_path, local_files_only=True)
    config = RunConfig(seeds=[1, 2, 3], batch_size=4, micro_batch_size=2, max_length=16, stride=4)
    model = JointL1Model(TinyEncoder(), hidden_size=HIDDEN, dropout=0.0)
    report = probe(model, tokenizer, config, torch.device("cpu"))
    assert report["precision"] == "fp32"
    assert report["peak_bytes"] is None  # CUDA 가 아니면 재지 않는다
    assert report["max_length"] == 16
