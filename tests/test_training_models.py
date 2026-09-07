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
    assert not registry.model("teacher_qwen3guard_stream").enabled
    assert not registry.model("l2_qwen3guard_gen_4b").enabled


def test_model_gate_rejects_metadata_drift() -> None:
    registry = load_model_registry()
    asset = registry.model("l1_ax_encoder")
    with pytest.raises(ValueError, match="parameter count drift"):
        registry.verify(
            "l1_ax_encoder",
            resolver=lambda _id, _revision: (asset.license, asset.gated, asset.parameters + 1),
        )


def test_license_allowance_is_scoped_by_purpose() -> None:
    """§6.2.2 leans on backbone/teacher all being Apache-2.0; baselines are only run."""
    registry = load_model_registry()
    assert registry.allowed_licenses_for("backbone") == {"apache-2.0"}
    assert registry.allowed_licenses_for("teacher") == {"apache-2.0"}
    assert "cc-by-4.0" in registry.allowed_licenses_for("baseline")

    baseline = registry.model("baseline_polyguard_qwen_smol")
    assert baseline.purpose == "baseline"
    verified = registry.verify(
        baseline.name,
        resolver=lambda _id, _rev: (baseline.license, baseline.gated, baseline.parameters),
    )
    assert verified.license == "CC-BY-4.0"


def test_a_training_model_may_not_borrow_the_baseline_allowance() -> None:
    registry = load_model_registry()
    teacher = registry.model("teacher_kanana")
    with pytest.raises(ValueError, match="not allowed for purpose 'teacher'"):
        registry.verify(
            "teacher_kanana",
            resolver=lambda _id, _rev: ("CC-BY-4.0", teacher.gated, teacher.parameters),
        )


def test_baselines_stay_out_of_the_default_download_plan() -> None:
    """A training run must not pull gigabytes of evaluation-only baselines."""
    from garim.training.assets import build_asset_plan
    from garim.training.sources import load_source_registry

    registry = load_model_registry()
    sources = load_source_registry()
    default = {item.name for item in build_asset_plan(registry, sources) if item.kind == "model"}
    widened = {
        item.name
        for item in build_asset_plan(registry, sources, include_baselines=True)
        if item.kind == "model"
    }
    assert "baseline_polyguard_qwen_smol" not in default
    assert "baseline_polyguard_qwen_smol" in widened
    assert "teacher_qwen3guard_stream" not in default
