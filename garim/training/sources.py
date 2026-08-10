"""Fail-closed source authorization for offline training and evaluation assets."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from garim.tls import https_context

Purpose = Literal["train", "eval", "value_dictionary", "schema_reference"]
LicenseResolver = Callable[[str, str], str]
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _license_key(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


class ForbiddenPath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    reason: str
    enforced_by: list[str] = Field(default_factory=list)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    enabled: bool = True
    use: Purpose
    hf_id: str | None = None
    url: str
    license: str | None = None
    license_url: str | None = None
    revision: str | None = None
    file: str | None = None
    sha256: str | None = None
    hf_config: str | None = None
    train_split: str | None = None
    eval_split: str | None = None
    note: str = ""

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str | None) -> str | None:
        if value is not None and not _COMMIT.fullmatch(value):
            raise ValueError("revision must be a full 40-character commit SHA")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must contain exactly 64 lowercase hex characters")
        return value


class ExcludedSource(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    license: str | None = None
    reason: str


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_licenses: list[str]
    forbidden_paths: list[ForbiddenPath] = Field(default_factory=list)
    sources: list[Source]
    excluded: list[ExcludedSource] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Authorization:
    source: Source
    verified_license: str
    share_alike: bool


def resolve_huggingface_license(hf_id: str, revision: str) -> str:
    """Resolve current HF license while asserting that the pinned revision still exists."""
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS host, escaped identifier
        f"https://huggingface.co/api/datasets/{hf_id}/revision/{revision}",
        headers={"User-Agent": "garim-source-gate/0.1"},
    )
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=15, context=https_context()
    ) as response:
        payload = json.load(response)
    license_value = payload.get("cardData", {}).get("license")
    if isinstance(license_value, list):
        if len(license_value) != 1:
            raise ValueError(f"{hf_id}: expected exactly one license, got {license_value!r}")
        license_value = license_value[0]
    if not isinstance(license_value, str) or not license_value.strip():
        raise ValueError(f"{hf_id}: missing license metadata at pinned revision")
    return license_value


class SourceRegistry:
    def __init__(self, manifest: SourceManifest, *, workspace_root: Path) -> None:
        self.manifest = manifest
        self.workspace_root = workspace_root.resolve()
        self._sources = {source.name: source for source in manifest.sources}
        if len(self._sources) != len(manifest.sources):
            raise ValueError("source names must be unique")
        self._allowed = {_license_key(item) for item in manifest.allowed_licenses}

    def source(self, name: str) -> Source:
        try:
            return self._sources[name]
        except KeyError as exc:
            raise ValueError(f"unregistered source: {name}") from exc

    def assert_path_allowed(self, path: str | Path) -> None:
        supplied = Path(path)
        lexical = supplied if supplied.is_absolute() else self.workspace_root / supplied
        relatives: list[str] = []
        for candidate in (lexical.absolute(), lexical.resolve()):
            try:
                relatives.append(candidate.relative_to(self.workspace_root).as_posix())
            except ValueError:
                relatives.append(candidate.as_posix().lstrip("/"))
        for item in self.manifest.forbidden_paths:
            prefix = item.path.removesuffix("/**").rstrip("/")
            if any(
                relative == prefix or relative.startswith(prefix + "/")
                for relative in relatives
            ):
                raise ValueError(f"forbidden training path {relatives[0]}: {item.reason}")

    def load_huggingface(
        self,
        name: str,
        *,
        purpose: Purpose,
        split: str,
        license_resolver: LicenseResolver = resolve_huggingface_license,
        dataset_loader: Callable[..., object] | None = None,
    ) -> object:
        """Authorize and load exactly the pinned HF revision and split."""
        source = self.authorize(
            name, purpose=purpose, license_resolver=license_resolver
        ).source
        if source.hf_id is None or source.revision is None:
            raise ValueError(f"{name}: not a HuggingFace source")
        expected_split = source.train_split if purpose == "train" else source.eval_split
        if expected_split is not None and split != expected_split:
            raise ValueError(f"{name}: expected split {expected_split!r}, got {split!r}")
        if dataset_loader is None:
            from datasets import load_dataset

            dataset_loader = load_dataset
        return dataset_loader(
            source.hf_id,
            source.hf_config,
            split=split,
            revision=source.revision,
        )

    def authorize(
        self,
        name: str,
        *,
        purpose: Purpose,
        local_file: str | Path | None = None,
        license_resolver: LicenseResolver = resolve_huggingface_license,
    ) -> Authorization:
        source = self.source(name)
        if not source.enabled:
            raise ValueError(f"{name}: source is disabled pending verification")
        if source.use != purpose:
            raise ValueError(
                f"{name}: registered for {source.use}, cannot authorize as {purpose}"
            )
        if local_file is not None:
            self.assert_path_allowed(local_file)

        if source.hf_id is not None:
            if source.revision is None:
                raise ValueError(f"{name}: HuggingFace source must pin a full revision")
            actual_license = license_resolver(source.hf_id, source.revision)
        else:
            if source.license is None:
                raise ValueError(f"{name}: non-HF source has no verified license")
            actual_license = source.license
            if purpose in {"train", "value_dictionary"}:
                if local_file is None or source.sha256 is None:
                    raise ValueError(f"{name}: local file and sha256 are required")
                actual_digest = _sha256(Path(local_file))
                if actual_digest != source.sha256:
                    raise ValueError(
                        f"{name}: sha256 mismatch (expected {source.sha256}, got {actual_digest})"
                    )

        if _license_key(actual_license) not in self._allowed:
            raise ValueError(f"{name}: license {actual_license!r} is not allowed")
        declared_license = source.license
        if declared_license is not None and _license_key(declared_license) != _license_key(
            actual_license
        ):
            raise ValueError(
                f"{name}: registry license {source.license!r} disagrees with {actual_license!r}"
            )
        return Authorization(
            source=source,
            verified_license=actual_license,
            share_alike=_license_key(actual_license) == "cc-by-sa-4.0",
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_registry(
    path: str | Path = "train/sources.yaml", *, workspace_root: str | Path | None = None
) -> SourceRegistry:
    manifest_path = Path(path)
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load source manifest {manifest_path}: {exc}") from exc
    manifest = SourceManifest.model_validate(payload)
    root = Path(workspace_root) if workspace_root is not None else manifest_path.parent.parent
    return SourceRegistry(manifest, workspace_root=root)
