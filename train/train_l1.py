#!/usr/bin/env python3
"""Fail-closed joint L1 training entrypoint; execution requires an explicit flag."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict
from pathlib import Path

from garim.training.config import RunConfig, load_run_config
from garim.training.data import CorpusRecord, load_jsonl
from garim.training.provenance import verify_training_input_manifest
from garim.training.readiness import validate_training_corpora


def _load_many(paths: list[str]) -> list[CorpusRecord]:
    return [record for path in paths for record in load_jsonl(path)]


def _assert_ready(
    pii_train: list[CorpusRecord],
    pii_validation: list[CorpusRecord],
    attack_train: list[CorpusRecord],
    attack_validation: list[CorpusRecord],
) -> dict[str, object]:
    return validate_training_corpora(
        pii_train,
        pii_validation,
        attack_train,
        attack_validation,
    )


def _device(torch, requested: str):  # noqa: ANN001
    if requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _train_seed(
    *,
    seed: int,
    encoder_dir: str,
    output_dir: Path,
    pii_train: list[CorpusRecord],
    pii_validation: list[CorpusRecord],
    attack_train: list[CorpusRecord],
    attack_validation: list[CorpusRecord],
    batch_size: int,
    epochs: int,
    max_length: int,
    stride: int,
    device_name: str,
    run_config: RunConfig,
) -> dict[str, object]:
    import torch
    from torch.optim.lr_scheduler import LambdaLR
    from torch.utils.data import DataLoader
    from transformers import AutoTokenizer

    from garim.training.dataset import EncodedCorpusDataset, JointBatchCollator
    from garim.training.engine import TrainingConfig, build_optimizer, train_epoch
    from garim.training.evaluation import evaluate_joint_model
    from garim.training.model import JointL1Model
    from garim.training.sampling import interleave_task_batches
    from garim.training.stopping import MultiTaskEarlyStopping

    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    device = _device(torch, device_name)
    tokenizer = AutoTokenizer.from_pretrained(
        encoder_dir, local_files_only=True, use_fast=True
    )
    if not tokenizer.is_fast:
        raise ValueError("L1 training requires a fast tokenizer with offset mappings")
    collator = JointBatchCollator(tokenizer)
    pii_dataset = EncodedCorpusDataset(
        tokenizer, pii_train, max_length=max_length, stride=stride
    )
    attack_dataset = EncodedCorpusDataset(
        tokenizer, attack_train, max_length=max_length, stride=stride
    )
    validation_dataset = EncodedCorpusDataset(
        tokenizer,
        [*pii_validation, *attack_validation],
        max_length=max_length,
        stride=stride,
    )
    generator = torch.Generator().manual_seed(seed)
    pii_loader = DataLoader(
        pii_dataset,
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collator,
        generator=generator,
    )

    def attack_loader():
        return DataLoader(
            attack_dataset,
            batch_size=batch_size,
            shuffle=True,
            collate_fn=collator,
            generator=generator,
        )

    model = JointL1Model.from_pretrained(encoder_dir, local_files_only=True).to(device)
    config = TrainingConfig(
        learning_rate=run_config.learning_rate,
        weight_decay=run_config.weight_decay,
        gradient_clip=run_config.gradient_clip,
        task_lambda=run_config.task_lambda,
        hard_label_alpha=run_config.hard_label_alpha,
        temperature=run_config.temperature,
        qwen_weight=run_config.qwen_weight,
        auxiliary_weight=run_config.auxiliary_weight,
        pii_o_weight=run_config.pii_o_weight,
    )
    optimizer = build_optimizer(model, config)
    steps_per_epoch = len(pii_loader) + math.ceil(
        len(pii_loader) / run_config.pii_per_attack
    )
    total_steps = max(1, steps_per_epoch * epochs)
    warmup_steps = max(1, round(total_steps * run_config.warmup_ratio))

    def lr_scale(step: int) -> float:
        if step < warmup_steps:
            return step / warmup_steps
        return max(0.0, (total_steps - step) / max(1, total_steps - warmup_steps))

    scheduler = LambdaLR(optimizer, lr_scale)
    stopping = MultiTaskEarlyStopping(patience=run_config.early_stopping_patience)
    history: list[dict[str, object]] = []
    best_score = float("-inf")
    seed_dir = output_dir / f"seed-{seed}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, epochs + 1):
        losses = train_epoch(
            model,
            interleave_task_batches(
                pii_loader,
                attack_loader,
                pii_per_attack=run_config.pii_per_attack,
            ),
            optimizer,
            device=device,
            config=config,
            scheduler=scheduler,
        )
        metrics = evaluate_joint_model(
            model,
            validation_dataset,
            collator,
            device=device,
            batch_size=batch_size,
        )
        row = {"epoch": epoch, "loss": asdict(losses), "metrics": metrics.as_dict()}
        history.append(row)
        score = 2 * metrics.pii_character_recall * metrics.attack_macro_recall / max(
            1e-12, metrics.pii_character_recall + metrics.attack_macro_recall
        )
        if score > best_score:
            best_score = score
            torch.save(model.state_dict(), seed_dir / "joint_l1.pt")
        if stopping.update(
            pii_recall=metrics.pii_character_recall,
            attack_macro_recall=metrics.attack_macro_recall,
        ):
            break
    tokenizer.save_pretrained(seed_dir / "encoder")
    result = {
        "seed": seed,
        "device": str(device),
        "best_joint_recall_hmean": best_score,
        "history": history,
    }
    (seed_dir / "training-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder-dir", required=True)
    parser.add_argument("--pii-train", action="append", required=True)
    parser.add_argument("--pii-validation", action="append", required=True)
    parser.add_argument("--attack-train", action="append", required=True)
    parser.add_argument("--attack-validation", action="append", required=True)
    parser.add_argument("--config", default="train/training.yaml")
    parser.add_argument("--asset-manifest", default="data/asset-manifest.json")
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument(
        "--input-manifest", default="data/training-input-manifest.json"
    )
    parser.add_argument("--output-dir", default="train/artifacts/l1")
    parser.add_argument("--seeds")
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--stride", type=int)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--dry-run-examples", type=int, default=32)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run_config = load_run_config(args.config)
    seeds = (
        [int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()]
        if args.seeds
        else run_config.seeds
    )
    epochs = args.epochs if args.epochs is not None else run_config.epochs
    batch_size = args.batch_size if args.batch_size is not None else run_config.batch_size
    max_length = args.max_length if args.max_length is not None else run_config.max_length
    stride = args.stride if args.stride is not None else run_config.stride
    if epochs < 1 or batch_size < 1 or max_length < 8 or stride >= max_length - 2:
        raise ValueError("invalid CLI override for epochs/batch-size/max-length/stride")

    pii_train = _load_many(args.pii_train)
    pii_validation = _load_many(args.pii_validation)
    attack_train = _load_many(args.attack_train)
    attack_validation = _load_many(args.attack_validation)
    report = _assert_ready(pii_train, pii_validation, attack_train, attack_validation)
    report["sealed_input_contract"] = verify_training_input_manifest(
        args.input_manifest,
        {
            "pii_train": args.pii_train,
            "pii_validation": args.pii_validation,
            "attack_train": args.attack_train,
            "attack_validation": args.attack_validation,
        },
        provenance_files={
            "asset_manifest": args.asset_manifest,
            "models_manifest": args.models_manifest,
            "sources_manifest": args.sources_manifest,
            "training_config": args.config,
        },
    )
    encoder_dir = Path(args.encoder_dir)
    if not encoder_dir.is_dir():
        raise ValueError(f"local encoder directory does not exist: {encoder_dir}")

    if not args.execute:
        import torch
        from transformers import AutoTokenizer

        from garim.training.dataset import EncodedCorpusDataset, JointBatchCollator
        from garim.training.model import JointL1Model

        tokenizer = AutoTokenizer.from_pretrained(
            encoder_dir, local_files_only=True, use_fast=True
        )
        collator = JointBatchCollator(tokenizer)
        sample = [
            *pii_train[: args.dry_run_examples],
            *attack_train[: args.dry_run_examples],
        ]
        encoded = EncodedCorpusDataset(
            tokenizer, sample, max_length=max_length, stride=stride
        )
        device = _device(torch, args.device)
        model = JointL1Model.from_pretrained(
            str(encoder_dir), local_files_only=True
        ).to(device)
        batch = collator(encoded.examples[: min(batch_size, len(encoded.examples))])
        model.eval()
        with torch.inference_mode():
            output = model(
                batch["input_ids"].to(device),
                batch["attention_mask"].to(device),
                batch["normalization_delta"].to(device),
                batch.get("token_type_ids").to(device)
                if batch.get("token_type_ids") is not None
                else None,
            )
        if output.pii_logits.shape[-1] != 29 or output.attack_logits.shape[-1] != 4:
            raise ValueError("joint model dry-run returned an invalid head shape")
        report["dry_run_encoded_windows"] = len(encoded)
        report["dry_run_forward"] = {
            "device": str(device),
            "batch": int(output.pii_logits.shape[0]),
            "tokens": int(output.pii_logits.shape[1]),
            "heads": [29, 4, 1, 1],
        }
        report["training_executed"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if len(seeds) < 3:
        raise ValueError("DESIGN requires at least three fixed seeds")
    results = [
        _train_seed(
            seed=seed,
            encoder_dir=str(encoder_dir),
            output_dir=Path(args.output_dir),
            pii_train=pii_train,
            pii_validation=pii_validation,
            attack_train=attack_train,
            attack_validation=attack_validation,
            batch_size=batch_size,
            epochs=epochs,
            max_length=max_length,
            stride=stride,
            device_name=args.device,
            run_config=run_config,
        )
        for seed in seeds
    ]
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.output_dir) / "three-seed-report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
