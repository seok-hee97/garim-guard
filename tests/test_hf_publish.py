"""HF 업로드의 공개 · 비공개 갈래 — 합성 주민등록번호가 든 파일이 공개 저장소로 가지 않는다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "hf_publish", Path(__file__).resolve().parents[1] / "tools" / "hf_publish.py"
)
hf_publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf_publish)  # type: ignore[union-attr]


def test_files_with_checksum_valid_identifiers_stay_private() -> None:
    for relative in (
        "corpus/pii-synthetic-train.jsonl",
        "corpus/kdpii-test.jsonl",
        "merged/pii-train.jsonl",
        "runpod/2026-10-27/merged/pii-validation.jsonl",
        "raw/CredData/meta/a.csv",
    ):
        assert hf_publish.data_bucket(relative) == "private", relative


def test_training_records_and_l0_silent_corpora_are_public() -> None:
    for relative in (
        "corpus/pii-negative-r2-test.jsonl",
        "corpus/t4-r2-test.jsonl",
        "corpus/attack-teacher-input-r2.jsonl",
        "runpod/2026-10-27-r2/final/train/artifacts/r2/verdict.json",
        "runpod/2026-10-27/corpus/teacher-gemma4.jsonl",
        "training-input-manifest.json",
    ):
        assert hf_publish.data_bucket(relative) == "public", relative


def test_model_copy_inside_records_is_skipped() -> None:
    path = "runpod/2026-10-27-r2/final/train/artifacts/l1-r2-onnx-rr/garim_l1.int8.onnx"
    assert hf_publish.data_bucket(path) == "skip"
