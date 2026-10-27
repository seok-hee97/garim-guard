"""PREREG_L1_R2 §4 판정표 — 통과선이 사전 등록 값대로 걸리는지."""

from __future__ import annotations

import json
from pathlib import Path

from bench.r2_verdict import verdict


def _ratio(value: float) -> dict:
    return {"value": value, "families": 3, "ci95": [value, value]}


def _write(
    directory: Path,
    model: str,
    *,
    detection: float,
    over: float,
    o2: float,
    o3: float,
    bc: float,
    kdpii: tuple[float, float],
    klue: float,
    account: float,
) -> None:
    (directory / f"{model}-r2-eval.json").write_text(
        json.dumps(
            {
                "O2_t4_r2_benign_fpr": _ratio(o2),
                "O3_pii_negative_rows_with_prediction": {"all_negatives": _ratio(o3)},
                "O3_bc_negative_rows_with_prediction": _ratio(bc),
                "O4_account_pair_positive_char_recall": _ratio(account),
                "S4_t4_r2_rows_with_pii": _ratio(0.0),
            }
        )
    )
    (directory / f"{model}-product-path.json").write_text(
        json.dumps(
            {
                "by_source": {
                    "KDPII": {"product_path": {"precision": kdpii[0], "recall": kdpii[1]}},
                    "KLUE-NER-validation": {"product_path": {"precision": 0.9, "recall": klue}},
                }
            }
        )
    )
    axis = {
        "reports": [
            {
                "judge": "garim-l1",
                "detection_rate": detection,
                "over_defense_rate": over,
                "by_category": {"A2_leaking": 0.1},
                "by_technique": {},
            }
        ]
    }
    for suffix in ("", "-600"):
        (directory / f"{model}-axis-b{suffix}.json").write_text(json.dumps(axis))
    confusion = {
        "NONE": {"NONE": 9, "INJECTION": 1, "JAILBREAK": 0, "SYSTEM_LEAK": 0},
        "INJECTION": {"NONE": 5, "INJECTION": 4, "JAILBREAK": 1, "SYSTEM_LEAK": 0},
    }
    name = "l1-eval.json" if model == "l1-r2-onnx" else "l1-onnx-eval.json"
    (directory / name).write_text(
        json.dumps({"by_source": {"deepset-prompt-injections": {"attack_confusion": confusion}}})
    )


R1 = dict(
    detection=0.342,
    over=0.007,
    o2=0.436,
    o3=0.517,
    bc=0.086,
    kdpii=(0.980, 0.977),
    klue=0.932,
    account=1.0,
)
PASSING = dict(
    detection=0.330,
    over=0.010,
    o2=0.05,
    o3=0.02,
    bc=0.02,
    kdpii=(0.970, 0.967),
    klue=0.922,
    account=0.90,
)


def test_boundary_values_pass(tmp_path: Path) -> None:
    _write(tmp_path, "l1-onnx", **R1)
    _write(tmp_path, "l1-r2-onnx", **PASSING)
    result = verdict(tmp_path)
    assert result["primary_pass"] == {"O1": True, "O2": True, "O3": True, "O4": True}
    assert result["adopt_r2"] is True
    assert result["R2"]["S3_deepset"] == {
        "injection_exact_recall": 0.4,
        "injection_any_attack_recall": 0.5,
        "fpr": 0.1,
    }


def test_each_primary_criterion_can_fail_alone(tmp_path: Path) -> None:
    _write(tmp_path, "l1-onnx", **R1)
    for key, value, failed in (
        ("detection", 0.321, "O1"),
        ("over", 0.011, "O1"),
        ("o2", 0.051, "O2"),
        ("bc", 0.021, "O3"),
        ("o3", 0.021, "O3"),
        ("kdpii", (0.969, 0.99), "O4"),
        ("klue", 0.921, "O4"),
        ("account", 0.89, "O4"),
    ):
        _write(tmp_path, "l1-r2-onnx", **{**PASSING, key: value})
        result = verdict(tmp_path)
        assert result["adopt_r2"] is False, key
        assert [k for k, ok in result["primary_pass"].items() if not ok] == [failed], key
