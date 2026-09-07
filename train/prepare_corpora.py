#!/usr/bin/env python3
"""Materialize reviewed raw sources into leakage-checked JSONL corpora."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from garim.training.adapters import (
    adapt_bc_augmented_negatives,
    adapt_deepset_translation_queue,
    adapt_kdpii_dialogues,
    adapt_klue_ner,
    adapt_polyguard_mix,
    adapt_polyguard_prompts,
    extract_bc_value_pools,
)
from garim.training.assets import asset_manifest_drift
from garim.training.data import CorpusRecord, validate_corpus, write_jsonl
from garim.training.sampling import deterministic_subset
from garim.training.sources import load_source_registry
from garim.training.split import fingerprint_text, split_kdpii_dialogues
from garim.training.synthetic import generate_attack_corpus, generate_pii_corpus


def _parquet_rows(paths: list[Path]) -> Iterator[dict[str, Any]]:
    if not paths:
        raise ValueError("no parquet files matched the pinned asset layout")
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise RuntimeError("install garim-guard[train] to prepare parquet datasets") from exc
    for path in paths:
        parquet_file = parquet.ParquetFile(path)
        for batch in parquet_file.iter_batches(batch_size=2048):
            yield from batch.to_pylist()


def _jsonl_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            yield row


def _load_asset_manifest(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assets = payload["assets"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"missing or invalid {path}; run train/download_assets.py --execute first"
        ) from exc
    return {str(item["name"]): item for item in assets}


def _assert_pinned_asset(
    assets: dict[str, dict[str, Any]], name: str, revision: str | None
) -> Path:
    item = assets.get(name)
    if item is None:
        raise ValueError(f"asset manifest has no completed entry for {name}")
    if item.get("revision") != revision:
        raise ValueError(f"{name}: downloaded revision does not match sources.yaml")
    path = Path(str(item["destination"]))
    if not path.is_dir():
        raise ValueError(f"{name}: downloaded asset directory is missing: {path}")
    files = item.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"{name}: asset manifest has no file checksums")
    drift = asset_manifest_drift(path, files)
    if any(drift.values()):
        raise ValueError(f"{name}: local asset drift: {drift}")
    return path


def _write_split_files(output: Path, prefix: str, records: list[CorpusRecord]) -> None:
    for split in ("train", "validation", "test"):
        selected = [record for record in records if record.split == split]
        if selected:
            write_jsonl(output / f"{prefix}-{split}.jsonl", selected)


def _public_corpora(
    *, asset_manifest: Path, sources_manifest: str, polyguard_limit: int | None
) -> tuple[dict[str, list[CorpusRecord]], list[dict[str, Any]], dict[str, list[str]]]:
    registry = load_source_registry(sources_manifest)
    assets = _load_asset_manifest(asset_manifest)
    corpora: dict[str, list[CorpusRecord]] = {}
    reports: list[dict[str, Any]] = []

    kdpii_source = registry.source("KDPII")
    kdpii_root = _assert_pinned_asset(assets, "KDPII", None)
    kdpii_file = kdpii_root / str(kdpii_source.file)
    registry.authorize("KDPII", purpose="train", local_file=kdpii_file)
    payload = json.loads(kdpii_file.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("KDPII root must be a JSON array")
    kdpii_splits = split_kdpii_dialogues(payload)
    kdpii_records: list[CorpusRecord] = []
    for split, dialogues in kdpii_splits.items():
        converted, report = adapt_kdpii_dialogues(dialogues, split=split)  # type: ignore[arg-type]
        kdpii_records.extend(converted)
        reports.append(report.as_dict())
    corpora["kdpii"] = kdpii_records

    klue_train_source = registry.source("KLUE-NER")
    klue_train_root = _assert_pinned_asset(
        assets, "KLUE-NER", klue_train_source.revision
    )
    converted, report = adapt_klue_ner(
        _parquet_rows(sorted((klue_train_root / "ner").glob("train-*.parquet"))),
        split="train",
        source_name="KLUE-NER",
    )
    corpora["klue_train"] = converted
    reports.append(report.as_dict())

    klue_eval_source = registry.source("KLUE-NER-validation")
    klue_eval_root = _assert_pinned_asset(
        assets, "KLUE-NER-validation", klue_eval_source.revision
    )
    converted, report = adapt_klue_ner(
        _parquet_rows(sorted((klue_eval_root / "ner").glob("validation-*.parquet"))),
        split="test",
        source_name="KLUE-NER-validation",
    )
    corpora["klue_eval"] = converted
    reports.append(report.as_dict())

    poly_train_source = registry.source("PolyGuardMix")
    poly_train_root = _assert_pinned_asset(
        assets, "PolyGuardMix", poly_train_source.revision
    )
    poly_train = list(
        adapt_polyguard_mix(
            _parquet_rows(sorted((poly_train_root / "data").glob("train-*.parquet")))
        )
    )

    poly_eval_source = registry.source("PolyGuardPrompts")
    poly_eval_root = _assert_pinned_asset(
        assets, "PolyGuardPrompts", poly_eval_source.revision
    )
    poly_eval = list(
        adapt_polyguard_prompts(
            _parquet_rows(sorted((poly_eval_root / "data").glob("test-*.parquet")))
        )
    )
    eval_fingerprints = {fingerprint_text(record.text) for record in poly_eval}
    eval_source_ids = {
        str(record.meta.get("source_id"))
        for record in poly_eval
        if record.meta.get("source_id") is not None
    }
    poly_train = [
        record
        for record in poly_train
        if fingerprint_text(record.text) not in eval_fingerprints
        and str(record.meta.get("source_id")) not in eval_source_ids
    ]
    # ★ 평가셋과 갈라낸 **뒤에** 뽑는다. 순서가 반대면 평가와 겹치는 행이 표본에
    #   들어갔다가 나중에 빠져 표본 크기가 요청보다 작아진다.
    #
    #   전량은 92,710행이다. §6 이 R2 풀을 ~1,920 으로 추정했으므로 전량은 설계 의도를
    #   크게 넘고 교사 3종 추론이 278k forward 가 된다. 기본값 20,000 은 합성 1,238 과
    #   합쳐 PII:공격 을 약 5:1 로 만들어 `pii_per_attack=4` 와 자연스럽게 맞물린다
    #   (고치기 전에는 88:1 이라 공격 1행을 5에폭에 109번 봤다).
    poly_selected = deterministic_subset(
        poly_train,
        polyguard_limit,
        key=lambda record: record.id,
        salt="polyguard-train",
    )
    corpora["polyguard_train"] = poly_selected
    corpora["polyguard_eval"] = poly_eval
    reports.append(
        {
            "sampling": "polyguard_train",
            "available": len(poly_train),
            "selected": len(poly_selected),
            "limit": polyguard_limit,
            "method": "stable sha256 rank over record id, order-independent",
        }
    )

    deep_train_source = registry.source("deepset-prompt-injections")
    deep_train_root = _assert_pinned_asset(
        assets, "deepset-prompt-injections", deep_train_source.revision
    )
    corpora["deepset_train_queue"] = list(
        adapt_deepset_translation_queue(
            _parquet_rows(sorted((deep_train_root / "data").glob("train-*.parquet"))),
            split="train",
        )
    )
    deep_eval_source = registry.source("deepset-prompt-injections-test")
    deep_eval_root = _assert_pinned_asset(
        assets, "deepset-prompt-injections-test", deep_eval_source.revision
    )
    corpora["deepset_eval_queue"] = list(
        adapt_deepset_translation_queue(
            _parquet_rows(sorted((deep_eval_root / "data").glob("test-*.parquet"))),
            split="test",
        )
    )

    bc_source = registry.source("bc-pii-ko")
    bc_root = _assert_pinned_asset(assets, "bc-pii-ko", bc_source.revision)
    value_pools, report = extract_bc_value_pools(_jsonl_rows(bc_root / "train.jsonl"))
    reports.append(report.as_dict())
    bc_eval_source = registry.source("bc-pii-ko-augmented-negative")
    bc_eval_root = _assert_pinned_asset(
        assets, "bc-pii-ko-augmented-negative", bc_eval_source.revision
    )
    corpora["bc_pii_augmented_negative"] = adapt_bc_augmented_negatives(
        _jsonl_rows(bc_eval_root / "train.jsonl")
    )
    return corpora, reports, value_pools


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-manifest", default="data/asset-manifest.json")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--output-dir", default="data/corpus")
    parser.add_argument("--synthetic-count", type=int, default=50_000)
    parser.add_argument(
        "--polyguard-limit",
        type=int,
        default=20_000,
        help="PolyGuardMix 한국어 학습 표본 크기. 0 이면 전량(92,710).",
    )
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument(
        "--ack-sensitive-synthetic-identifiers",
        action="store_true",
        help="acknowledge that checksum-valid generated identifiers may match real values",
    )
    args = parser.parse_args()
    if not args.ack_sensitive_synthetic_identifiers:
        raise SystemExit(
            "refusing to generate checksum-valid identifiers without "
            "--ack-sensitive-synthetic-identifiers; outputs must remain local and ignored"
        )

    output = Path(args.output_dir)
    public: dict[str, list[CorpusRecord]] = {}
    adapter_reports: list[dict[str, Any]] = []
    value_pools: dict[str, list[str]] = {}
    if not args.skip_public:
        public, adapter_reports, value_pools = _public_corpora(
            asset_manifest=Path(args.asset_manifest),
            sources_manifest=args.sources_manifest,
            polyguard_limit=args.polyguard_limit or None,
        )
    pii_synthetic = generate_pii_corpus(
        count=args.synthetic_count,
        seed=args.seed,
        value_pools=value_pools,
    )
    attack_synthetic = generate_attack_corpus(seed=args.seed)
    validate_corpus(pii_synthetic)
    validate_corpus(attack_synthetic)
    _write_split_files(output, "pii-synthetic", pii_synthetic)
    _write_split_files(output, "attack-synthetic", attack_synthetic)

    reports: list[dict[str, Any]] = [
        {"corpus": "pii-synthetic", **validate_corpus(pii_synthetic)},
        {"corpus": "attack-synthetic", **validate_corpus(attack_synthetic)},
    ]
    if not args.skip_public:
        for name, records in public.items():
            _write_split_files(output, name, records)
            reports.append({"corpus": name, **validate_corpus(records)})
        reports.extend({"adapter": report} for report in adapter_reports)

    report_path = output / "preparation-report.json"
    report_path.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"prepared corpora; report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
