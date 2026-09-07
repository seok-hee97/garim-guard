from __future__ import annotations

import sys
from pathlib import Path

from garim.training.data import CorpusRecord, write_jsonl
from garim.training.teacher_io import TeacherArtifact, load_teacher_artifacts
from garim.training.teachers import load_teacher_policy
from train import run_teacher


class _BatchBackend:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def predict_many(self, records: list[CorpusRecord]) -> list[TeacherArtifact]:
        self.batches.append([record.id for record in records])
        spec = load_teacher_policy().teachers["qwen3guard_gen"]
        return [
            TeacherArtifact(
                id=record.id,
                teacher="qwen3guard_gen",
                model_id=spec.model_id,
                revision=spec.revision,
                label="NONE",
                confidence=1.0,
                class_probabilities={"JAILBREAK": 0.0},
            )
            for record in records
        ]


def _record(identifier: str) -> CorpusRecord:
    return CorpusRecord(
        id=identifier,
        task="attack",
        text=f"정상 요청 {identifier}",
        source="fixture",
        split="train",
    )


def test_run_teacher_resumes_and_batches_pending_rows(
    tmp_path: Path, monkeypatch
) -> None:
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    records = [_record(f"row-{index}") for index in range(5)]
    write_jsonl(input_path, records)
    spec = load_teacher_policy().teachers["qwen3guard_gen"]
    output_path.write_text(
        TeacherArtifact(
            id="row-0",
            teacher="qwen3guard_gen",
            model_id=spec.model_id,
            revision=spec.revision,
            label="NONE",
            confidence=1.0,
            class_probabilities={"JAILBREAK": 0.0},
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    backend = _BatchBackend()
    monkeypatch.setattr(run_teacher, "load_teacher_backend", lambda *args, **kwargs: backend)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_teacher.py",
            "--teacher",
            "qwen3guard_gen",
            "--model-dir",
            str(tmp_path / "unused-model"),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--batch-size",
            "2",
            "--execute",
        ],
    )

    assert run_teacher.main() == 0
    assert backend.batches == [["row-1", "row-2"], ["row-3", "row-4"]]
    assert set(load_teacher_artifacts(output_path)) == {record.id for record in records}


def test_run_teacher_rejects_stale_resumable_output(
    tmp_path: Path, monkeypatch
) -> None:
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "output.jsonl"
    write_jsonl(input_path, [_record("row-0")])
    spec = load_teacher_policy().teachers["qwen3guard_gen"]
    output_path.write_text(
        TeacherArtifact(
            id="other-row",
            teacher="qwen3guard_gen",
            model_id=spec.model_id,
            revision=spec.revision,
            label="NONE",
            confidence=1.0,
            class_probabilities={"JAILBREAK": 0.0},
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["run_teacher.py"])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_teacher.py",
            "--teacher",
            "qwen3guard_gen",
            "--model-dir",
            str(tmp_path / "unused-model"),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--execute",
        ],
    )

    try:
        run_teacher.main()
    except ValueError as exc:
        assert "IDs outside the selected input" in str(exc)
    else:
        raise AssertionError("stale resumable output must fail closed")
