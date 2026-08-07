"""Explicit pinned-asset download plan and local readiness inspection."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from garim.training.models import ModelAsset, ModelRegistry
from garim.training.sources import Source, SourceRegistry

AssetKind = Literal["model", "dataset"]


@dataclass(frozen=True, slots=True)
class AssetPlan:
    kind: AssetKind
    name: str
    identifier: str
    revision: str | None
    destination: str
    purpose: str


_DATASET_PATTERNS = {
    "KLUE-NER": ["README.md", "ner/train-*.parquet"],
    "KLUE-NER-validation": ["README.md", "ner/validation-*.parquet"],
    "PolyGuardMix": ["README.md", "data/train-*.parquet"],
    "PolyGuardPrompts": ["README.md", "data/test-*.parquet"],
    "bc-pii-ko": ["README.md", "train.jsonl"],
    "bc-pii-ko-augmented-negative": ["README.md", "train.jsonl"],
    "deepset-prompt-injections": ["README.md", "data/train-*.parquet"],
    "deepset-prompt-injections-test": ["README.md", "data/test-*.parquet"],
}


def build_asset_plan(
    model_registry: ModelRegistry,
    source_registry: SourceRegistry,
    *,
    model_root: str | Path = "models/pinned",
    data_root: str | Path = "data/raw",
    include_teachers: bool = True,
    include_eval: bool = True,
) -> list[AssetPlan]:
    models = [
        asset
        for asset in model_registry.manifest.models
        if asset.enabled
        and asset.purpose in ({"backbone", "teacher"} if include_teachers else {"backbone"})
    ]
    sources = [
        source
        for source in source_registry.manifest.sources
        if source.enabled
        and source.use != "schema_reference"
        and (include_eval or source.use != "eval")
    ]
    model_base = Path(model_root)
    data_base = Path(data_root)
    plan = [
        AssetPlan(
            "model",
            asset.name,
            asset.model_id,
            asset.revision,
            str(model_base / asset.name),
            asset.purpose,
        )
        for asset in models
    ]
    for source in sources:
        identifier = source.hf_id or source.url
        destination_name = (
            "bc-pii-ko"
            if source.name == "bc-pii-ko-augmented-negative"
            else source.name
        )
        plan.append(
            AssetPlan(
                "dataset",
                source.name,
                identifier,
                source.revision,
                str(data_base / destination_name),
                source.use,
            )
        )
    return plan


def _download_hf_model(asset: ModelAsset, destination: Path) -> None:
    from huggingface_hub import snapshot_download

    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=asset.model_id,
        revision=asset.revision,
        local_dir=destination,
    )


def _download_hf_dataset(source: Source, destination: Path) -> None:
    from huggingface_hub import snapshot_download

    if source.hf_id is None or source.revision is None:
        raise ValueError(f"{source.name}: expected a pinned HuggingFace dataset")
    patterns = _DATASET_PATTERNS.get(source.name)
    if patterns is None:
        raise ValueError(f"{source.name}: no reviewed download pattern")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=source.hf_id,
        repo_type="dataset",
        revision=source.revision,
        allow_patterns=patterns,
        local_dir=destination,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def asset_file_manifest(destination: Path) -> dict[str, dict[str, object]]:
    return {
        path.relative_to(destination).as_posix(): {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file() and ".cache" not in path.relative_to(destination).parts
    }


def asset_manifest_drift(
    destination: Path, expected: object
) -> dict[str, list[str]]:
    """Compare an asset directory exactly, including files added after download."""
    if not isinstance(expected, dict):
        return {"missing": [], "modified": [], "unrecorded": ["<invalid-manifest>"]}
    current = asset_file_manifest(destination) if destination.is_dir() else {}
    expected_names = {str(name) for name in expected}
    current_names = set(current)
    modified = sorted(
        name
        for name in expected_names & current_names
        if not isinstance(expected[name], dict)
        or current[name].get("size") != expected[name].get("size")
        or current[name].get("sha256") != expected[name].get("sha256")
    )
    return {
        "missing": sorted(expected_names - current_names),
        "modified": modified,
        "unrecorded": sorted(current_names - expected_names),
    }


def _download_kdpii(source: Source, destination: Path) -> None:
    if source.file is None or source.sha256 is None:
        raise ValueError("KDPII manifest must define file and sha256")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / source.file
    temporary = output.with_suffix(output.suffix + ".part")
    url = f"{source.url.rstrip('/')}/files/{source.file}?download=1"
    request = urllib.request.Request(url, headers={"User-Agent": "garim-asset-fetch/0.1"})
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,  # noqa: S310
            temporary.open("wb") as stream,
        ):
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        digest = _sha256(temporary)
        if digest != source.sha256:
            raise ValueError(
                f"KDPII sha256 mismatch (expected {source.sha256}, got {digest})"
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def download_assets(
    model_registry: ModelRegistry,
    source_registry: SourceRegistry,
    plan: list[AssetPlan],
    *,
    manifest_path: str | Path = "data/asset-manifest.json",
) -> Path:
    """Download an already-reviewed plan. Callers must require an explicit execute flag."""
    completed: list[dict[str, object]] = []
    for item in plan:
        destination = Path(item.destination)
        if item.kind == "model":
            verified = model_registry.verify(item.name)
            _download_hf_model(verified.asset, destination)
        else:
            source = source_registry.source(item.name)
            if source.hf_id is not None:
                source_registry.authorize(item.name, purpose=source.use)
                _download_hf_dataset(source, destination)
            elif source.name == "KDPII":
                _download_kdpii(source, destination)
                source_registry.authorize(
                    source.name,
                    purpose=source.use,
                    local_file=destination / str(source.file),
                )
            else:
                raise ValueError(f"{source.name}: no reviewed downloader")
        completed.append({**asdict(item), "files": asset_file_manifest(destination)})

    output = Path(manifest_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "assets": completed,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def inspect_asset_plan(plan: list[AssetPlan]) -> list[dict[str, object]]:
    status: list[dict[str, object]] = []
    for item in plan:
        destination = Path(item.destination)
        files = list(destination.rglob("*")) if destination.is_dir() else []
        status.append(
            {
                **asdict(item),
                "present": destination.is_dir() and any(path.is_file() for path in files),
                "files": sum(path.is_file() for path in files),
            }
        )
    return status


def verify_asset_manifest(
    path: str | Path, plan: list[AssetPlan]
) -> list[dict[str, object]]:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        completed = {item["name"]: item for item in payload["assets"]}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return [{"name": "asset-manifest", "ok": False, "detail": str(exc)}]

    results: list[dict[str, object]] = []
    for planned in plan:
        item = completed.get(planned.name)
        if item is None:
            results.append({"name": planned.name, "ok": False, "detail": "not recorded"})
            continue
        destination = Path(planned.destination)
        ok = (
            item.get("revision") == planned.revision
            and item.get("destination") == planned.destination
            and isinstance(item.get("files"), dict)
            and bool(item["files"])
        )
        drift = {"missing": [], "modified": [], "unrecorded": []}
        if ok:
            drift = asset_manifest_drift(destination, item["files"])
            ok = not any(drift.values())
        results.append(
            {
                "name": planned.name,
                "ok": ok,
                "detail": {"destination": planned.destination, **drift},
            }
        )
    return results
