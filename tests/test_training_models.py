from __future__ import annotations

import pytest

from garim.training.models import load_model_registry


def test_selected_models_are_pinned_and_open() -> None:
    registry = load_model_registry()
    backbone = registry.model("l1_ax_encoder")
    assert backbone.enabled
    assert backbone.license == "Apache-2.0"
    assert not backbone.gated
    assert len(backbone.revision) == 40
    assert not registry.model("l2_qwen3guard_gen_4b").enabled


def test_model_gate_rejects_metadata_drift() -> None:
    registry = load_model_registry()
    asset = registry.model("l1_ax_encoder")
    with pytest.raises(ValueError, match="parameter count drift"):
        registry.verify(
            "l1_ax_encoder",
            resolver=lambda _id, _revision: (asset.license, asset.gated, asset.parameters + 1),
        )
