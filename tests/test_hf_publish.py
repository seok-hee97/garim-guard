"""HF 업로드의 공개 · 비공개 갈래 — 합성 주민등록번호가 든 파일이 공개 저장소로 가지 않는다."""

from __future__ import annotations

import importlib.util
import json
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


def test_names_outside_the_old_rules_no_longer_default_to_public() -> None:
    """★ 전수 검토(2026-10-28) — 규칙에 없는 이름은 전부 공개로 갔다."""
    assert hf_publish.data_bucket("corpus/pii-synthetic-train.jsonl.tmp") == "skip"
    for relative in (
        "corpus/pii-synthetic-r3-train.jsonl",
        "runpod/2026-11-02/merged/pii-train.jsonl",
        "runpod/2026-10-27-r2/data/merged/pii-validation.jsonl",
        "elsewhere/raw/source.csv",
    ):
        assert hf_publish.data_bucket(relative) == "private", relative


def _valid_rrn() -> str:
    digits = "900101123456"
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    check = (11 - sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11) % 10
    return f"{digits[:6]}-{digits[6:]}{check}"


def test_public_candidates_are_read_before_publishing(tmp_path: Path) -> None:
    clean = tmp_path / "clean.jsonl"
    clean.write_text('{"text": "안녕하세요"}\n', encoding="utf-8")
    assert hf_publish.publish_blocker(clean) is None
    rrn = _valid_rrn()
    # JSONL 은 합성기의 줄바꿈 표기를 두 글자 `\\n` 으로 저장한다.
    for body in (f"{rrn}\n", json.dumps({"text": f"{rrn[:7]}\n{rrn[7:]}"}) + "\n"):
        leaked = tmp_path / "leaked.jsonl"
        leaked.write_text(body, encoding="utf-8")
        assert hf_publish.publish_blocker(leaked) == "checksum-valid resident-number candidate"
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    assert hf_publish.publish_blocker(binary) == "not UTF-8 text"
    key = tmp_path / "run.log"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    assert hf_publish.publish_blocker(key) == "secret (private_key_header)"


def test_staging_is_rebuilt_from_the_current_plan(tmp_path: Path) -> None:
    """★ 지난 실행의 공개 스테이징이 남아 비공개로 옮긴 파일이 다시 공개로 올라갔다."""
    source = tmp_path / "a.jsonl"
    source.write_text("v1\n", encoding="utf-8")
    workdir = tmp_path / "staging"
    hf_publish.stage({"public": [(source, "corpus/a.jsonl")], "private": []}, workdir)
    (workdir / "public" / ".cache").mkdir()
    hf_publish.stage({"public": [], "private": [(source, "corpus/a.jsonl")]}, workdir)
    assert not (workdir / "public" / "corpus").exists()
    assert (workdir / "public" / ".cache").is_dir()  # 이어 올리기 기록은 둔다
    assert (workdir / "private" / "corpus" / "a.jsonl").read_text(encoding="utf-8") == "v1\n"
