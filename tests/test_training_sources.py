from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from garim.training.sources import (
    Source,
    SourceManifest,
    SourceRegistry,
    load_source_registry,
)


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

    authorization = registry.authorize("PolyGuardMix", purpose="train", license_resolver=resolver)
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


def _source(**overrides: object) -> Source:
    base = {
        "name": "probe",
        "use": "eval",
        "hf_id": "org/dataset",
        "url": "https://huggingface.co/datasets/org/dataset",
        "license": "CC-BY-4.0",
        "revision": "a" * 40,
        "eval_split": "test",
    }
    return Source(**{**base, **overrides})  # type: ignore[arg-type]


def _one_source_registry(source: Source, allowed: list[str]) -> SourceRegistry:
    return SourceRegistry(
        SourceManifest(allowed_licenses=allowed, sources=[source]),
        workspace_root=Path("."),
    )


def test_a_deliberate_divergence_is_allowed_and_reported() -> None:
    """★ 이 회귀가 자산 다운로드를 멈춰 세웠다.

    `deepset/prompt-injections` 는 top-level card 가 Apache-2.0, nested dataset_info 가
    CC-BY-4.0 이라 **보수적으로 후자를 적용**하기로 한 원천이다. 게이트가 "등록값 ==
    카드값" 만 볼 줄 알아서 이 **의도된** 불일치에 걸렸다. 카드값을 따라 등록값을
    바꾸면 보수적 선택이 조용히 사라진다.
    """
    registry = _one_source_registry(
        _source(card_license="apache-2.0", note="nested dataset_info 가 더 좁아서 그쪽을 쓴다"),
        ["cc-by-4.0", "apache-2.0"],
    )
    authorization = registry.authorize(
        "probe", purpose="eval", license_resolver=lambda *_: "apache-2.0"
    )
    assert authorization.verified_license == "cc-by-4.0"  # 우리가 지는 의무
    assert authorization.card_license == "apache-2.0"  # 상류가 선언한 것


def test_upstream_drift_is_still_caught_under_a_divergence() -> None:
    """카드 쪽으로 감시하므로 상류가 바뀌면 여전히 막혀야 한다 — fail-closed 유지."""
    registry = _one_source_registry(
        _source(card_license="apache-2.0", note="보수적으로 좁게 적용"),
        ["cc-by-4.0", "apache-2.0", "mit"],
    )
    with pytest.raises(ValueError, match="disagrees"):
        registry.authorize("probe", purpose="eval", license_resolver=lambda *_: "mit")


def test_a_forbidden_upstream_cannot_be_laundered_by_redeclaring_it() -> None:
    """★ 상류가 안 준 권리를 스스로에게 줄 수는 없다.

    카드가 `cc-by-nc-4.0`(비상업) 인 원천에 `license: CC-BY-4.0` 을 적어 "우리는 이
    조건으로 쓴다" 고 통과시킬 수 있으면 허용 목록이 무의미해진다. 보수적 재선언은
    오직 더 **좁히는** 방향만 허용된다.
    """
    registry = _one_source_registry(
        _source(card_license="cc-by-nc-4.0", note="상류가 NC 인데 우리가 넓혀 쓰겠다는 시도"),
        ["cc-by-4.0"],
    )
    with pytest.raises(ValueError, match="not allowed"):
        registry.authorize("probe", purpose="eval", license_resolver=lambda *_: "cc-by-nc-4.0")


def test_a_silent_divergence_is_refused_at_load_time() -> None:
    """이유 없는 불일치는 다음 사람이 '낡은 매니페스트' 로 읽고 카드값에 맞춰 버린다."""
    with pytest.raises(ValidationError, match="note explaining"):
        _source(card_license="apache-2.0", note="")


def test_card_license_must_actually_diverge() -> None:
    """같은 값을 적어 두면 아무 뜻도 없으면서 검사만 통과시킨다."""
    with pytest.raises(ValidationError, match="deliberate divergence"):
        _source(card_license="CC-BY-4.0", note="같은 값")
