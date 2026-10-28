from __future__ import annotations

from pathlib import Path

import pytest

from garim.training.config import RunConfig, load_run_config
from garim.training.data import CorpusRecord, write_jsonl
from garim.training.provenance import (
    create_training_input_manifest,
    verify_training_input_manifest,
    write_training_input_manifest,
)


def test_pinned_training_config_matches_design() -> None:
    config = load_run_config()
    assert config.seeds == [13, 37, 73]
    assert config.max_length == 512
    assert config.pii_per_attack == 4
    assert config.epochs == 5
    assert config.warmup_ratio == 0.1
    assert config.pii_o_weight == 0.25


def test_training_config_rejects_duplicate_seeds_and_nonprogressing_stride() -> None:
    payload = load_run_config().model_dump()
    with pytest.raises(ValueError, match="unique"):
        RunConfig.model_validate({**payload, "seeds": [1, 1, 2]})
    with pytest.raises(ValueError, match="stride"):
        RunConfig.model_validate({**payload, "stride": 510})


def test_training_input_manifest_detects_post_seal_drift(tmp_path: Path) -> None:
    records = {
        "pii_train": CorpusRecord(
            id="pii-train",
            task="pii",
            text="학습 개인정보 문장",
            source="fixture",
            split="train",
        ),
        "pii_validation": CorpusRecord(
            id="pii-validation",
            task="pii",
            text="검증 개인정보 문장",
            source="fixture",
            split="validation",
        ),
        "attack_train": CorpusRecord(
            id="attack-train",
            task="attack",
            text="학습 공격 문장",
            source="fixture",
            split="train",
            attack_label="INJECTION",
        ),
        "attack_validation": CorpusRecord(
            id="attack-validation",
            task="attack",
            text="검증 공격 문장",
            source="fixture",
            split="validation",
            attack_label="NONE",
        ),
    }
    paths: dict[str, list[str]] = {}
    for name, record in records.items():
        path = tmp_path / f"{name}.jsonl"
        write_jsonl(path, [record])
        paths[name] = [str(path)]
    provenance = tmp_path / "training.yaml"
    provenance.write_text("fixed: true\n", encoding="utf-8")
    provenance_files = {"training_config": str(provenance)}
    manifest = tmp_path / "training-input-manifest.json"
    write_training_input_manifest(
        manifest,
        create_training_input_manifest(paths, provenance_files=provenance_files),
    )
    verify_training_input_manifest(manifest, paths, provenance_files=provenance_files)

    Path(paths["attack_train"][0]).write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content drift"):
        verify_training_input_manifest(manifest, paths, provenance_files=provenance_files)
