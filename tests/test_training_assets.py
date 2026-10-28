from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from garim.training.assets import (
    AssetPlan,
    asset_file_manifest,
    verify_asset_manifest,
    verify_cached_hf_snapshot,
)


def test_asset_manifest_detects_local_drift(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    destination.mkdir()
    payload = destination / "config.json"
    payload.write_text("reviewed", encoding="utf-8")
    plan = AssetPlan(
        kind="model",
        name="fixture",
        identifier="org/model",
        revision="a" * 40,
        destination=str(destination),
        purpose="backbone",
    )
    manifest = tmp_path / "asset-manifest.json"
    manifest.write_text(
        json.dumps({"assets": [{**asdict(plan), "files": asset_file_manifest(destination)}]}),
        encoding="utf-8",
    )
    assert verify_asset_manifest(manifest, [plan])[0]["ok"] is True
    payload.write_text("mutated", encoding="utf-8")
    assert verify_asset_manifest(manifest, [plan])[0]["ok"] is False


def test_asset_manifest_detects_unrecorded_files(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    destination.mkdir()
    (destination / "config.json").write_text("reviewed", encoding="utf-8")
    plan = AssetPlan(
        kind="model",
        name="fixture",
        identifier="org/model",
        revision="a" * 40,
        destination=str(destination),
        purpose="backbone",
    )
    manifest = tmp_path / "asset-manifest.json"
    manifest.write_text(
        json.dumps({"assets": [{**asdict(plan), "files": asset_file_manifest(destination)}]}),
        encoding="utf-8",
    )
    (destination / "unreviewed.py").write_text("raise RuntimeError", encoding="utf-8")
    status = verify_asset_manifest(manifest, [plan])[0]
    assert status["ok"] is False
    assert status["detail"]["unrecorded"] == ["unreviewed.py"]


def test_asset_manifest_detects_identifier_drift(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    destination.mkdir()
    (destination / "config.json").write_text("reviewed", encoding="utf-8")
    plan = AssetPlan(
        kind="model",
        name="fixture",
        identifier="org/model",
        revision="a" * 40,
        destination=str(destination),
        purpose="backbone",
    )
    manifest = tmp_path / "asset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        **asdict(plan),
                        "identifier": "attacker/substitute",
                        "files": asset_file_manifest(destination),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert verify_asset_manifest(manifest, [plan])[0]["ok"] is False


def test_cached_hf_snapshot_requires_pinned_tree_hash_match(tmp_path: Path) -> None:
    destination = tmp_path / "asset"
    destination.mkdir()
    payload = destination / "config.json"
    content = b"reviewed"
    payload.write_bytes(content)
    revision = "a" * 40
    tree_directory = destination / ".cache" / "huggingface" / "trees"
    tree_directory.mkdir(parents=True)
    blob_id = hashlib.sha1(  # noqa: S324 - fixture for Git blob identity
        f"blob {len(content)}\0".encode() + content
    ).hexdigest()
    (tree_directory / f"{revision}.json").write_text(
        json.dumps(
            {
                "files": {
                    "config.json": {
                        "size": len(content),
                        "blob_id": blob_id,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    verify_cached_hf_snapshot(destination, revision)

    payload.write_bytes(b"mutated!")
    with pytest.raises(ValueError, match="cached Git blob mismatch"):
        verify_cached_hf_snapshot(destination, revision)
