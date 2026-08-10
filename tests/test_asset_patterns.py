"""자산 내려받기 패턴 검토 게이트. 무엇을 받는지 사람이 봤는지가 이 파일의 주제다."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from garim.training.assets import _ALLOWED_ASSET_SUFFIXES, _DATASET_PATTERNS, build_asset_plan
from garim.training.models import load_model_registry
from garim.training.sources import load_source_registry


def _planned_hf_datasets() -> list[str]:
    plan = build_asset_plan(
        load_model_registry("train/models.yaml"),
        load_source_registry("train/sources.yaml"),
    )
    return [
        item.name
        for item in plan
        if getattr(item, "kind", None) == "dataset"
        and not str(getattr(item, "identifier", "")).startswith("http")
    ]


def test_every_planned_dataset_has_a_reviewed_pattern() -> None:
    """★ 이 회귀가 자산 수집을 두 번째로 멈춰 세웠다.

    패턴 표에 8개만 있고 평가 데이터셋 셋이 비어 있어 `--execute` 가
    `KoreanGuardrail: no reviewed download pattern` 으로 죽었다. 게이트는 제 일을 한
    것이고 — 무엇을 받을지 사람이 보라는 요구다 — 빠진 것은 그 검토였다.
    """
    missing = [name for name in _planned_hf_datasets() if name not in _DATASET_PATTERNS]
    assert not missing, f"검토되지 않은 데이터셋: {missing}"


@pytest.mark.parametrize(("name", "patterns"), sorted(_DATASET_PATTERNS.items()))
def test_no_pattern_admits_executable_files(name: str, patterns: list[str]) -> None:
    """★ 데이터만 받고 실행 가능한 것은 받지 않는다.

    `kimchunsik03/KoreanGuardrail` 은 `ko_obfuscator.py`·`build_benchmark.py` 같은
    제3자 스크립트를 데이터와 같이 배포한다. 평가에 쓰지 않는 코드를 작업 트리에
    내려두면 언젠가 누가 임포트하거나 실행한다. `*` 같은 포괄 패턴도 같은 이유로
    막는다 — 지금 저장소에 스크립트가 없어도 다음 revision 에 생길 수 있다.
    """
    for pattern in patterns:
        suffix = PurePosixPath(pattern).suffix
        assert suffix, f"{name}: {pattern!r} 는 확장자가 없어 무엇이든 받는다"
        assert "*" not in suffix, f"{name}: {pattern!r} 는 확장자를 와일드카드로 열어 둔다"
        assert suffix in _ALLOWED_ASSET_SUFFIXES, f"{name}: {pattern!r} 는 데이터 파일이 아니다"


def test_the_korean_guardrail_pattern_excludes_its_scripts_and_seeds() -> None:
    """검토 결과를 값으로 고정한다 — 다음 사람이 편의로 `*` 를 넣지 못하게."""
    patterns = _DATASET_PATTERNS["KoreanGuardrail"]
    assert "benchmark.jsonl" in patterns
    assert not any("seeds" in pattern for pattern in patterns)
    assert not any(pattern.endswith(".py") for pattern in patterns)


def test_xl_safetybench_pattern_matches_the_registered_config() -> None:
    """`hf_config: jailbreak` 로 등록했으면 `cultural` 쪽을 받을 이유가 없다."""
    registry = load_source_registry("train/sources.yaml")
    assert registry.source("XL-SafetyBench").hf_config == "jailbreak"
    patterns = _DATASET_PATTERNS["XL-SafetyBench"]
    assert any("jailbreak" in pattern for pattern in patterns)
    assert not any("cultural" in pattern for pattern in patterns)
