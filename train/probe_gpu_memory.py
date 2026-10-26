#!/usr/bin/env python3
"""0b — `train/training.yaml` 설정으로 학습 최악 배치를 GPU 에서 먼저 돌려 본다.

교사 3종이 끝난 뒤 학습 첫 스텝에서 OOM 을 만나지 않게, pod 에서 교사보다 먼저 잰다.
최대 할당량이 VRAM 의 `--limit`(기본 85%)을 넘으면 `micro_batch_size` 를 줄이라고 하고 멈춘다.
논리 배치(`batch_size`)는 줄이지 않는다 — 그건 학습 설정을 바꾸는 일이다.

    python train/probe_gpu_memory.py --device cuda
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garim.training.config import load_run_config  # noqa: E402
from garim.training.memory_probe import probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--encoder-dir", default="models/pinned/l1_ax_encoder")
    parser.add_argument("--config", default="train/training.yaml")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--limit", type=float, default=0.85)
    args = parser.parse_args()

    import torch
    from transformers import AutoTokenizer

    from garim.training.model import JointL1Model

    run_config = load_run_config(args.config)
    tokenizer = AutoTokenizer.from_pretrained(
        args.encoder_dir, local_files_only=True, use_fast=True
    )
    model = JointL1Model.from_pretrained(args.encoder_dir, local_files_only=True)
    report = probe(model, tokenizer, run_config, torch.device(args.device))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    fraction = report["peak_fraction"]
    if fraction is None:
        print("CUDA 가 아니라 최대 메모리를 재지 않았다 — 배선만 확인했다.")
        return 0
    if fraction > args.limit:
        micro = run_config.micro_batch_size or run_config.batch_size
        print(
            f"최대 메모리가 VRAM 의 {fraction:.0%} 다(한도 {args.limit:.0%}). "
            f"train/training.yaml 의 micro_batch_size 를 {max(1, micro // 2)} 로 줄이고 다시 돈다.",
            file=sys.stderr,
        )
        return 1
    print(f"OK — 최대 메모리 VRAM 의 {fraction:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
