"""B1 — decoupled `head_dim` 모델을 transformers>=5 에서 로드하기 위한 shim.

`kanana-safeguard-prompt-2.1b` 는 hidden 1792 / heads 24 / head_dim 128 인데
`LlamaConfig.validate_architecture` 가 `head_dim` 을 보지 않아 `AutoConfig` 단계에서
막힌다. 설정이 유효하고 검증자가 틀린 경우라, 그 조건일 때만 검증자를 걷어낸다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from garim.training.compat import (
    allow_decoupled_head_dim,
    config_needs_decoupled_head_shim,
    read_local_config,
)

KANANA = {"hidden_size": 1792, "num_attention_heads": 24, "head_dim": 128}
CONVENTIONAL = {"hidden_size": 768, "num_attention_heads": 12, "head_dim": 64}


def test_shim_engages_only_for_the_decoupled_case() -> None:
    assert config_needs_decoupled_head_shim(KANANA)
    assert not config_needs_decoupled_head_shim(CONVENTIONAL)
    # head_dim 을 선언하지 않았으면 검증자가 맞다 — 우회하지 않는다.
    assert not config_needs_decoupled_head_shim({"hidden_size": 1792, "num_attention_heads": 24})
    assert not config_needs_decoupled_head_shim({})
    assert not config_needs_decoupled_head_shim(
        {"hidden_size": 1792, "num_attention_heads": 0, "head_dim": 128}
    )


def test_read_local_config_round_trips(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps(KANANA), encoding="utf-8")
    assert read_local_config(tmp_path) == KANANA


def test_read_local_config_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read local model config"):
        read_local_config(tmp_path)


class _FakeConfig:
    """Stands in for `LlamaConfig` so this test needs no transformers install."""


def _validate_architecture() -> None:  # pragma: no cover - identity only
    raise AssertionError("should never run inside the shim")


_validate_architecture.__name__ = "validate_architecture"


def _other_validator() -> None:  # pragma: no cover - identity only
    ...


def test_shim_removes_only_the_architecture_validator_and_restores_it() -> None:
    original = (_validate_architecture, _other_validator)
    _FakeConfig.__class_validators__ = original

    with allow_decoupled_head_dim((_FakeConfig,)) as engaged:
        assert engaged
        names = [getattr(item, "__name__", "") for item in _FakeConfig.__class_validators__]
        assert "validate_architecture" not in names
        assert "_other_validator" in names

    assert _FakeConfig.__class_validators__ == original


def test_shim_restores_the_validator_even_when_loading_raises() -> None:
    original = (_validate_architecture,)
    _FakeConfig.__class_validators__ = original
    with pytest.raises(RuntimeError), allow_decoupled_head_dim((_FakeConfig,)):
        raise RuntimeError("load failed")
    assert _FakeConfig.__class_validators__ == original


def test_shim_is_a_no_op_when_the_validator_is_absent() -> None:
    _FakeConfig.__class_validators__ = (_other_validator,)
    with allow_decoupled_head_dim((_FakeConfig,)) as engaged:
        assert not engaged
