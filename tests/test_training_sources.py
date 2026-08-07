from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from garim.training.sources import SourceRegistry, load_source_registry


def _registry() -> SourceRegistry:
    return load_source_registry("train/sources.yaml", workspace_root=Path.cwd())


def test_attack_train_and_external_test_sources_are_distinct() -> None:
    registry = _registry()
    assert registry.source("PolyGuardMix").use == "train"
    assert registry.source("PolyGuardMix").train_split == "train"
    assert registry.source("PolyGuardPrompts").use == "eval"
    assert registry.source("PolyGuardPrompts").eval_split == "test"


def test_hf_authorization_checks_pinned_license() -> None:
    registry = _registry()
    source = registry.source("PolyGuardMix")
    seen: list[tuple[str, str]] = []

    def resolver(hf_id: str, revision: str) -> str:
        seen.append((hf_id, revision))
        return "cc-by-4.0"

    authorization = registry.authorize(
        "PolyGuardMix", purpose="train", license_resolver=resolver
    )
    assert authorization.verified_license == "cc-by-4.0"
    assert seen == [(source.hf_id, source.revision)]


def test_hf_loader_pins_revision_config_and_split() -> None:
    registry = _registry()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def loader(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return {"loaded": True}

    loaded = registry.load_huggingface(
        "KLUE-NER",
        purpose="train",
        split="train",
        license_resolver=lambda _id, _revision: "cc-by-sa-4.0",
        dataset_loader=loader,
    )
    source = registry.source("KLUE-NER")
    assert loaded == {"loaded": True}
    assert calls == [
        (
            (source.hf_id, "ner"),
            {"split": "train", "revision": source.revision},
        )
    ]


def test_hf_loader_rejects_test_split_as_training() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="expected split"):
        registry.load_huggingface(
            "PolyGuardMix",
            purpose="train",
            split="test",
            license_resolver=lambda _id, _revision: "cc-by-4.0",
            dataset_loader=lambda *args, **kwargs: None,
        )


def test_registry_rejects_license_drift() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="not allowed"):
        registry.authorize(
            "PolyGuardMix",
            purpose="train",
            license_resolver=lambda _id, _revision: "cc-by-nc-4.0",
        )


def test_non_hf_training_source_requires_matching_hash(tmp_path: Path) -> None:
    registry = _registry()
    payload = tmp_path / "PII_dataset_V3.json"
    payload.write_bytes(b"not the official dataset")
    assert hashlib.sha256(payload.read_bytes()).hexdigest() != registry.source("KDPII").sha256
    with pytest.raises(ValueError, match="sha256 mismatch"):
        registry.authorize("KDPII", purpose="train", local_file=payload)


def test_aihub_path_is_rejected_even_without_opening_file() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="forbidden training path"):
        registry.assert_path_allowed(Path.cwd() / "data/aihub/71926/source.json")
