"""ONNX export and CPU dynamic quantization for the joint L1 contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER

if TYPE_CHECKING:
    import torch

    from garim.training.model import JointL1Model

PII_LABELS = [
    "O",
    *[
        item
        for label in (
            "KR_RRN",
            "KR_FRN",
            "KR_PASSPORT",
            "KR_DL",
            "KR_BIZNO",
            "KR_CORPNO",
            "KR_NHI",
            "KR_ACCOUNT",
            "CARD",
            "PHONE",
            "EMAIL",
            "PERSON",
            "ADDRESS",
            "ORG_ID",
        )
        for item in (f"B-{label}", f"I-{label}")
    ],
]


def export_joint_onnx(
    model: JointL1Model,
    output_path: str | Path,
    *,
    input_ids: torch.Tensor,
    attention_mask: torch.Tensor,
    normalization_delta: torch.Tensor,
    # 17 made the exporter fall back to the ONNX C-API down-converter
    # ("version conversion is not supported by the onnxscript version converter").
    # 18 is the lowest opset the current exporter emits natively.
    opset_version: int = 18,
) -> Path:
    """Export four named outputs; this function never quantizes implicitly.

    ★ Two defects lived here until the round-trip test was written, and neither could
    surface while every torch test was skipped:

    1. ``model.eval()`` was called on the joint model, but ``torch.onnx.export`` reads the
       training flag of the module it is handed — the freshly built ``Wrapper``, which is
       in train mode. Dropout was therefore traced as *active* into an inference graph.
    2. ``dynamic_axes`` is the legacy TorchScript-exporter API. torch flipped
       ``torch.onnx.export`` to the dynamo exporter, whose compatibility shim collapsed
       ``batch`` and ``sequence`` into one constraint and produced a graph that failed
       ONNX shape inference ("Inferred shape and existing shape differ in dimension 0")
       the moment ``quantize_dynamic`` touched it. The whole export step was broken.
    """
    import torch

    class Wrapper(torch.nn.Module):
        def __init__(self, joint_model: JointL1Model) -> None:
            super().__init__()
            self.joint_model = joint_model

        def forward(self, input_ids, attention_mask, normalization_delta):  # noqa: ANN001
            output = self.joint_model(input_ids, attention_mask, normalization_delta)
            return (
                output.pii_logits,
                output.attack_logits,
                output.romanized_logits,
                output.honorific_logits,
            )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    dynamic = torch.export.Dim.DYNAMIC
    wrapper = Wrapper(model)
    wrapper.eval()  # dropout must be inactive in the exported graph
    torch.onnx.export(
        wrapper,
        (input_ids, attention_mask, normalization_delta),
        destination,
        input_names=["input_ids", "attention_mask", "normalization_delta"],
        output_names=[
            "pii_logits",
            "attack_logits",
            "romanized_logits",
            "honorific_logits",
        ],
        dynamic_shapes={
            "input_ids": {0: dynamic, 1: dynamic},
            "attention_mask": {0: dynamic, 1: dynamic},
            "normalization_delta": {0: dynamic},
        },
        opset_version=opset_version,
        dynamo=True,
    )
    return destination


def quantize_joint_onnx(source: str | Path, destination: str | Path) -> Path:
    """Use signed per-channel weights, the CPU transformer-safe default to verify in W1.

    ★ The pre-processing pass is required, not cosmetic. The dynamo exporter emits
    ``value_info`` entries for initializers; ``quantize_dynamic`` transposes the ``Gemm``
    B operands and then re-runs shape inference against that stale ``value_info``, failing
    with ``Inferred shape and existing shape differ in dimension 0: (24) vs (4)`` — the
    head weight ``[4, 24]`` read after transposition. ``quant_pre_process`` is the path
    onnxruntime itself asks for in the warning this used to emit on every run.

    ★ 회귀 — 실제 A.X 로 내보낸 그래프에서 양자화가 죽었다. `quant_pre_process` 의 symbolic
    shape inference 가 ModernBERT 의 동적 `Range`(rotary·sliding window 마스크)에서
    `_infer_Range … assert len(x) == 1` 로 멈춘다. 작은 대역 인코더에는 `Range` 가 없어
    왕복 테스트가 통과했고, RunPod 에서 학습을 다 마친 **뒤** 처음 드러날 자리였다(로컬
    CPU 미니 학습으로 찾았다). symbolic 패스를 끄면 위의 stale ``value_info`` 가 되살아나므로
    (`775 vs 4` — 공격 헤드 입력 768 + 델타 7), 그 원인 자체를 지운다: 익스포터가 남긴 중간
    ``value_info`` 를 비우고 ONNX shape inference 가 새로 채우게 한다.
    """
    import tempfile

    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.shape_inference import quant_pre_process

    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workspace:
        model = onnx.load(str(source_path))
        del model.graph.value_info[:]
        cleaned = Path(workspace) / "cleaned.onnx"
        onnx.save(model, str(cleaned))
        prepared = Path(workspace) / "prepared.onnx"
        quant_pre_process(str(cleaned), str(prepared), skip_symbolic_shape=True)
        quantize_dynamic(
            model_input=str(prepared),
            model_output=str(destination_path),
            weight_type=QuantType.QInt8,
            per_channel=True,
            # ★ AVX2 · VNNI 없는 x86(Zen2 · Zen3 · Ice Lake 이전 Intel)에서 onnxruntime 의
            #   U8S8 행렬곱은 `vpmaddubsw` 의 int16 포화로 값이 깨진다. RunPod EPYC 7452 에서
            #   R1 · R2 INT8 이 FP32 와 33행 중 17~22행 다른 판정을 냈다(Mac 은 2행).
            #   7비트 가중치(reduce_range)면 0행이었다.
            reduce_range=True,
        )
    return destination_path


def write_l1_metadata(output_dir: str | Path) -> Path:
    destination = Path(output_dir) / "labels.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "pii_labels": PII_LABELS,
                "attack_labels": ["NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK"],
                # ★ 손으로 적어 두면 계약이 갈린다. 실제로 갈렸다 — `space` 를
                #   feature 에서 뺀 뒤에도 여기는 8칸을 적고 있었고, 런타임 어댑터는
                #   이 파일을 읽어 입력을 만든다(`OnnxL1Backend`). 학습·내보내기·추론이
                #   서로 다른 폭을 믿는 상태였다.
                "normalization_delta_order": list(NORMALIZATION_DELTA_ORDER),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination
