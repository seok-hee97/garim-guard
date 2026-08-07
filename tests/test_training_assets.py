from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from garim.training.assets import AssetPlan, asset_file_manifest, verify_asset_manifest


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
        json.dumps(
            {"assets": [{**asdict(plan), "files": asset_file_manifest(destination)}]}
        ),
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
        json.dumps(
            {"assets": [{**asdict(plan), "files": asset_file_manifest(destination)}]}
        ),
        encoding="utf-8",
    )
    (destination / "unreviewed.py").write_text("raise RuntimeError", encoding="utf-8")
    status = verify_asset_manifest(manifest, [plan])[0]
    assert status["ok"] is False
    assert status["detail"]["unrecorded"] == ["unreviewed.py"]
