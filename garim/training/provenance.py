"""Immutable training-input manifest created after all translation and labeling steps."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from garim.training.data import CorpusRecord, load_jsonl
from garim.training.readiness import validate_training_corpora

TrainingPaths = dict[str, list[str]]
SCHEMA_VERSION = 2


def file_sha256(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_path_key(path: str | Path) -> str:
    """Location key for the seal — relative to the working directory when possible.

    ★ schema 1 stored ``Path.resolve()`` and compared it verbatim, so the seal only
    verified on the machine and directory that produced it. Copying the tree was enough
    to fail it, which defeats both the "prepare here, train on the GPU box" flow and
    third-party reproduction of a public research artifact. Content identity still comes
    from ``sha256``; the path is only there to catch "you passed a different file".
    """
    source = Path(path).resolve()
    try:
        return source.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return source.as_posix()


def git_commit() -> str | None:
    """Best-effort code revision for the seal; ``None`` outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def _file_entry(path: str | Path, *, records: int | None = None) -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError(f"training provenance file does not exist: {source}")
    entry: dict[str, Any] = {
        "path": manifest_path_key(source),
        "bytes": source.stat().st_size,
        "sha256": file_sha256(source),
    }
    if records is not None:
        entry["records"] = records
    return entry


def _load_groups(paths: TrainingPaths) -> dict[str, list[CorpusRecord]]:
    required = {"pii_train", "pii_validation", "attack_train", "attack_validation"}
    if set(paths) != required:
        raise ValueError(f"training input groups must be exactly {sorted(required)}")
    return {
        name: [record for path in group_paths for record in load_jsonl(path)]
        for name, group_paths in paths.items()
    }


def create_training_input_manifest(
    paths: TrainingPaths,
    *,
    provenance_files: dict[str, str],
) -> dict[str, Any]:
    groups = _load_groups(paths)
    contract = validate_training_corpora(
        groups["pii_train"],
        groups["pii_validation"],
        groups["attack_train"],
        groups["attack_validation"],
    )
    inputs: dict[str, list[dict[str, Any]]] = {}
    for name, group_paths in paths.items():
        inputs[name] = [
            _file_entry(path, records=len(load_jsonl(path))) for path in group_paths
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "inputs": inputs,
        "provenance": {
            name: _file_entry(path) for name, path in sorted(provenance_files.items())
        },
        "contract": contract,
    }


def write_training_input_manifest(
    path: str | Path, payload: dict[str, Any]
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)
    return destination


def _verify_entry(entry: object, expected_path: str | Path) -> None:
    if not isinstance(entry, dict):
        raise ValueError("training input manifest contains an invalid file entry")
    source = Path(expected_path).resolve()
    expected_key = manifest_path_key(source)
    if entry.get("path") != expected_key:
        raise ValueError(
            f"training input path drift: expected {expected_key}, "
            f"recorded {entry.get('path')!r}"
        )
    if not source.is_file():
        raise ValueError(f"training input file is missing: {source}")
    if entry.get("bytes") != source.stat().st_size or entry.get("sha256") != file_sha256(
        source
    ):
        raise ValueError(f"training input content drift: {source}")


def verify_training_input_manifest(
    manifest_path: str | Path,
    paths: TrainingPaths,
    *,
    provenance_files: dict[str, str],
) -> dict[str, Any]:
    source = Path(manifest_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid training input manifest: {source}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported training input manifest schema "
            f"{payload.get('schema_version')!r} (expected {SCHEMA_VERSION}); "
            "re-run train/finalize_training_inputs.py to reseal"
        )
    recorded_inputs = payload.get("inputs")
    if not isinstance(recorded_inputs, dict) or set(recorded_inputs) != set(paths):
        raise ValueError("training input manifest group mismatch")
    for name, group_paths in paths.items():
        entries = recorded_inputs.get(name)
        if not isinstance(entries, list) or len(entries) != len(group_paths):
            raise ValueError(f"training input manifest file count mismatch: {name}")
        for entry, path in zip(entries, group_paths, strict=True):
            _verify_entry(entry, path)
            if entry.get("records") != len(load_jsonl(path)):
                raise ValueError(f"training input record count drift: {Path(path).resolve()}")

    recorded_provenance = payload.get("provenance")
    if not isinstance(recorded_provenance, dict) or set(recorded_provenance) != set(
        provenance_files
    ):
        raise ValueError("training provenance file set mismatch")
    for name, path in provenance_files.items():
        _verify_entry(recorded_provenance[name], path)

    groups = _load_groups(paths)
    return validate_training_corpora(
        groups["pii_train"],
        groups["pii_validation"],
        groups["attack_train"],
        groups["attack_validation"],
    )
