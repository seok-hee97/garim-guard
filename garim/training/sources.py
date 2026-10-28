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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # 우리가 **적용하는** 라이선스 — 받아들이는 의무다.
    license: str | None = None
    # ★ 상류 카드가 **선언한** 라이선스. 우리가 적용하는 것과 다를 때만 적는다.
    #
    #   `deepset/prompt-injections` 가 그렇다 — top-level card 는 Apache-2.0 인데
    #   nested `dataset_info` 는 CC-BY-4.0 이라 **보수적으로 후자를 적용**하기로 했다.
    #   그런데 게이트가 "등록값 == 카드값" 만 볼 줄 알아서 이 **의도된** 불일치에
    #   걸려 자산 다운로드가 통째로 멈췄다.
    #
    #   값을 카드값으로 맞춰서 넘기면 보수적 선택이 사라진다. 대신 두 값을 따로 적고
    #   게이트가 **카드값 쪽**으로 상류 변동을 감시하게 한다 — fail-closed 는 그대로다.
    card_license: str | None = None
    license_url: str | None = None
    revision: str | None = None
    file: str | None = None
    sha256: str | None = None
    hf_config: str | None = None
    train_split: str | None = None
    eval_split: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def explain_a_deliberate_license_divergence(self) -> Source:
        """카드값과 적용값이 다르면 **이유를 적게 강제한다.**

        조용한 불일치는 다음 사람이 "낡은 매니페스트" 로 읽고 카드값에 맞춰 버린다.
        그 순간 보수적으로 잡아둔 의무가 사라지는데 아무 경보도 안 울린다.
        """
        if self.card_license is None:
            return self
        if _license_key(self.card_license) == _license_key(self.license or ""):
            raise ValueError("card_license is only for a deliberate divergence; drop it")
        if not self.note.strip():
            raise ValueError(
                "a license divergence must carry a note explaining why the stricter "
                "license is applied"
            )
        return self

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
    # 적용 라이선스 — 귀속 표기와 의무는 이쪽을 따른다.
    verified_license: str
    # 상류 카드가 선언한 것. 둘이 다르면 우리가 더 보수적으로 잡았다는 뜻이다.
    card_license: str
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
                relative == prefix or relative.startswith(prefix + "/") for relative in relatives
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
        source = self.authorize(name, purpose=purpose, license_resolver=license_resolver).source
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
            raise ValueError(f"{name}: registered for {source.use}, cannot authorize as {purpose}")
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

        # ★ 검사 셋의 **순서가 안전 성질을 만든다.**
        #
        # (1) 상류의 실제 라이선스가 허용 목록 안인가. 이것이 먼저여야 한다 — 그렇지
        #     않으면 카드가 `cc-by-nc-4.0` 인 원천에 `license: CC-BY-4.0` 을 적어
        #     "우리는 이 조건으로 쓴다" 며 통과시킬 수 있다. **상류가 안 준 권리를
        #     스스로에게 줄 수는 없다.** 보수적 재선언은 오직 더 **좁히는** 방향만이다.
        if _license_key(actual_license) not in self._allowed:
            raise ValueError(f"{name}: license {actual_license!r} is not allowed")
        # (2) 상류 변동 감시. `card_license` 를 적어 둔 원천은 등록값과 카드값이
        #     **일부러** 다르므로 카드 쪽으로 맞춰본다 — fail-closed 는 그대로다.
        expected_card = source.card_license or source.license
        if expected_card is not None and _license_key(expected_card) != _license_key(
            actual_license
        ):
            raise ValueError(
                f"{name}: registry license {expected_card!r} disagrees with {actual_license!r}"
            )
        # (3) 우리가 받아들이기로 한 의무도 목록 안이어야 한다.
        applied_license = source.license or actual_license
        if _license_key(applied_license) not in self._allowed:
            raise ValueError(f"{name}: applied license {applied_license!r} is not allowed")
        return Authorization(
            source=source,
            # 정규형으로 낸다 — 매니페스트 표기(`CC-BY-4.0`)와 API 표기(`cc-by-4.0`)가
            # 갈리는데, 이 값을 귀속 표기에 쓰므로 한 가지로 고정해야 한다.
            verified_license=_license_key(applied_license),
            card_license=_license_key(actual_license),
            # 적용 라이선스로 판정한다. 카드가 Apache 라도 우리가 CC-BY-SA 를 적용하기로
            # 했다면 ShareAlike 의무를 져야 한다.
            share_alike=_license_key(applied_license) == "cc-by-sa-4.0",
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
