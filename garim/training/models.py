"""Pinned model metadata gate. Verification uses HF API and never downloads weights."""

from __future__ import annotations

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

ModelPurpose = Literal["backbone", "teacher", "l2_candidate", "baseline"]
ModelResolver = Callable[[str, str], tuple[str, bool, int]]
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ModelAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    model_id: str
    revision: str
    purpose: ModelPurpose
    license: str
    gated: bool
    parameters: int
    enabled: bool = True
    note: str = ""

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _COMMIT.fullmatch(value):
            raise ValueError("revision must be a full 40-character commit SHA")
        return value


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_licenses: list[str]
    # ★ 용도별로 나눈다. §6.2.2 는 "교사 셋과 학생이 전부 Apache-2.0" 이라는 사실에
    #   기대어 "증류 산출 가중치에 파생 조항이 걸리지 않는다" 고 결론했다. 그 보장은
    #   backbone·teacher 에만 필요하다. §7.3 베이스라인은 돌려서 수치만 보고하고
    #   가중치를 재배포하지 않으므로 조건이 다르다 — 전역 목록을 넓히면 그 구분이
    #   사라지므로 별도 목록으로 둔다. 비우면 `allowed_licenses` 를 그대로 쓴다.
    baseline_allowed_licenses: list[str] = Field(default_factory=list)
    models: list[ModelAsset]


@dataclass(frozen=True, slots=True)
class VerifiedModel:
    asset: ModelAsset
    license: str
    gated: bool
    parameters: int


def resolve_huggingface_model(model_id: str, revision: str) -> tuple[str, bool, int]:
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS host
        f"https://huggingface.co/api/models/{model_id}/revision/{revision}",
        headers={"User-Agent": "garim-model-gate/0.1"},
    )
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=15, context=https_context()
    ) as response:
        payload = json.load(response)
    license_value = payload.get("cardData", {}).get("license")
    gated = payload.get("gated", False)
    parameters = payload.get("safetensors", {}).get("total")
    if not isinstance(license_value, str) or not isinstance(parameters, int):
        raise ValueError(f"{model_id}: incomplete model metadata")
    return license_value, bool(gated), parameters


class ModelRegistry:
    def __init__(self, manifest: ModelManifest) -> None:
        self.manifest = manifest
        self._models = {asset.name: asset for asset in manifest.models}
        if len(self._models) != len(manifest.models):
            raise ValueError("model names must be unique")
        self._allowed = {license_value.lower() for license_value in manifest.allowed_licenses}
        self._baseline_allowed = {
            license_value.lower() for license_value in manifest.baseline_allowed_licenses
        } or self._allowed

    def allowed_licenses_for(self, purpose: ModelPurpose) -> set[str]:
        return self._baseline_allowed if purpose == "baseline" else self._allowed

    def model(self, name: str) -> ModelAsset:
        try:
            return self._models[name]
        except KeyError as exc:
            raise ValueError(f"unregistered model: {name}") from exc

    def verify(
        self, name: str, *, resolver: ModelResolver = resolve_huggingface_model
    ) -> VerifiedModel:
        asset = self.model(name)
        if not asset.enabled:
            raise ValueError(f"{name}: model is disabled pending task-specific validation")
        license_value, gated, parameters = resolver(asset.model_id, asset.revision)
        if license_value.lower() not in self.allowed_licenses_for(asset.purpose):
            raise ValueError(
                f"{name}: license {license_value!r} is not allowed for purpose {asset.purpose!r}"
            )
        if license_value.lower() != asset.license.lower():
            raise ValueError(f"{name}: model license drift")
        if gated != asset.gated:
            raise ValueError(f"{name}: gated status drift")
        if parameters != asset.parameters:
            raise ValueError(f"{name}: parameter count drift")
        return VerifiedModel(asset, license_value, gated, parameters)


def load_model_registry(path: str | Path = "train/models.yaml") -> ModelRegistry:
    manifest_path = Path(path)
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load model manifest {manifest_path}: {exc}") from exc
    return ModelRegistry(ModelManifest.model_validate(payload))
