"""Immutable final-evaluation seal tied to the exact training inputs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from garim.training.data import CorpusRecord, load_jsonl
from garim.training.provenance import file_sha256, git_commit, manifest_path_key
from garim.training.readiness import validate_sealed_evaluation_corpus
from garim.training.split import fingerprint_text

SCHEMA_VERSION = 1


def _entry(path: str | Path, *, records: int | None = None) -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError(f"evaluation provenance file does not exist: {source}")
    payload: dict[str, Any] = {
        "path": manifest_path_key(source),
        "bytes": source.stat().st_size,
        "sha256": file_sha256(source),
    }
    if records is not None:
        payload["records"] = records
    return payload


def _path_from_key(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("evaluation manifest contains an invalid path")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _verify_entry(entry: object, expected_path: str | Path | None = None) -> Path:
    if not isinstance(entry, dict):
        raise ValueError("evaluation manifest contains an invalid file entry")
    source = (
        Path(expected_path).resolve()
        if expected_path is not None
        else _path_from_key(entry.get("path"))
    )
    if entry.get("path") != manifest_path_key(source):
        raise ValueError(f"evaluation input path drift: {source}")
    if not source.is_file():
        raise ValueError(f"evaluation input file is missing: {source}")
    if entry.get("bytes") != source.stat().st_size or entry.get("sha256") != file_sha256(
        source
    ):
        raise ValueError(f"evaluation input content drift: {source}")
    return source


def _verified_training_records(training_manifest_path: str | Path) -> list[CorpusRecord]:
    source = Path(training_manifest_path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid training input manifest: {source}") from exc
    if payload.get("schema_version") != 2:
        raise ValueError("evaluation seal requires training input manifest schema 2")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("training input manifest has no input groups")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("training input manifest has no provenance files")
    for entry in provenance.values():
        _verify_entry(entry)
    records: list[CorpusRecord] = []
    for entries in inputs.values():
        if not isinstance(entries, list):
            raise ValueError("training input manifest contains an invalid input group")
        for entry in entries:
            path = _verify_entry(entry)
            rows = load_jsonl(path)
            if not isinstance(entry, dict) or entry.get("records") != len(rows):
                raise ValueError(f"training input record count drift: {path}")
            records.extend(rows)
    return records


def _assert_disjoint(
    evaluation_records: Sequence[CorpusRecord], training_records: Sequence[CorpusRecord]
) -> None:
    training_fingerprints = {fingerprint_text(record.text) for record in training_records}
    overlap = sorted(
        record.id
        for record in evaluation_records
        if fingerprint_text(record.text) in training_fingerprints
    )
    if overlap:
        raise ValueError(
            "evaluation text fingerprints overlap training inputs: "
            f"{overlap[:5]}{' ...' if len(overlap) > 5 else ''}"
        )


def create_evaluation_input_manifest(
    paths: Sequence[str | Path],
    *,
    training_manifest_path: str | Path,
    provenance_files: dict[str, str | Path],
) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one evaluation input is required")
    evaluation_records = [record for path in paths for record in load_jsonl(path)]
    contract = validate_sealed_evaluation_corpus(evaluation_records)
    training_records = _verified_training_records(training_manifest_path)
    _assert_disjoint(evaluation_records, training_records)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "inputs": [_entry(path, records=len(load_jsonl(path))) for path in paths],
        "training_manifest": _entry(training_manifest_path),
        "provenance": {
            name: _entry(path) for name, path in sorted(provenance_files.items())
        },
        "contract": contract,
    }


def write_evaluation_input_manifest(path: str | Path, payload: dict[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)
    return destination


def verify_evaluation_input_manifest(
    manifest_path: str | Path,
    paths: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    source = Path(manifest_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid evaluation input manifest: {source}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported evaluation input manifest schema {payload.get('schema_version')!r}"
        )
    entries = payload.get("inputs")
    if not isinstance(entries, list) or not entries:
        raise ValueError("evaluation input manifest has no inputs")
    if paths is not None and len(entries) != len(paths):
        raise ValueError("evaluation input manifest file count mismatch")
    resolved = [
        _verify_entry(entry, None if paths is None else paths[index])
        for index, entry in enumerate(entries)
    ]
    evaluation_records: list[CorpusRecord] = []
    for entry, path in zip(entries, resolved, strict=True):
        rows = load_jsonl(path)
        if not isinstance(entry, dict) or entry.get("records") != len(rows):
            raise ValueError(f"evaluation input record count drift: {path}")
        evaluation_records.extend(rows)

    training_entry = payload.get("training_manifest")
    training_manifest = _verify_entry(training_entry)
    training_records = _verified_training_records(training_manifest)
    _assert_disjoint(evaluation_records, training_records)

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("evaluation manifest has no provenance files")
    for entry in provenance.values():
        _verify_entry(entry)

    contract = validate_sealed_evaluation_corpus(evaluation_records)
    return {
        **contract,
        "training_records": len(training_records),
        "sealed": True,
    }
