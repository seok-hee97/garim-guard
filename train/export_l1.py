#!/usr/bin/env python3
"""Export a locally trained joint checkpoint. No remote model access is allowed."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder-dir", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sequence-length", type=int, default=128)
    args = parser.parse_args()

    import torch
    from transformers import AutoTokenizer

    from garim.training.export import (
        export_joint_onnx,
        quantize_joint_onnx,
        write_l1_metadata,
    )
    from garim.training.model import JointL1Model

    output_dir = Path(args.output_dir)
    model = JointL1Model.from_pretrained(args.encoder_dir, local_files_only=True)
    state = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model = model.float()

    tokenizer = AutoTokenizer.from_pretrained(args.encoder_dir, local_files_only=True)
    tokenizer.save_pretrained(output_dir)
    write_l1_metadata(output_dir)

    input_ids = torch.zeros((1, args.sequence_length), dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    delta = torch.zeros((1, 8), dtype=torch.float32)
    fp32_path = export_joint_onnx(
        model,
        output_dir / "garim_l1.fp32.onnx",
        input_ids=input_ids,
        attention_mask=attention_mask,
        normalization_delta=delta,
    )
    quantize_joint_onnx(fp32_path, output_dir / "garim_l1.int8.onnx")
    print(f"exported local model to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
