from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from garim.training.data import CorpusRecord, write_jsonl
from garim.training.evaluation_seal import (
    create_evaluation_input_manifest,
    verify_evaluation_input_manifest,
    write_evaluation_input_manifest,
)
from garim.training.provenance import (
    create_training_input_manifest,
    write_training_input_manifest,
)


@pytest.fixture
def in_directory() -> Iterator[None]:
    original = Path.cwd()
    try:
        yield
    finally:
        os.chdir(original)


def _training_record(identifier: str, task: str, split: str) -> CorpusRecord:
    return CorpusRecord(
        id=identifier,
        task=task,
        text=f"{identifier}의 고유 문장",
        source="fixture",
        split=split,
        attack_label="NONE" if task == "attack" else None,
    )


def _sealed_training(root: Path) -> Path:
    records = {
        "pii_train": _training_record("pii-train", "pii", "train"),
        "pii_validation": _training_record("pii-validation", "pii", "validation"),
        "attack_train": _training_record("attack-train", "attack", "train"),
        "attack_validation": _training_record(
            "attack-validation", "attack", "validation"
        ),
    }
    paths: dict[str, list[str]] = {}
    for name, record in records.items():
        path = root / f"{name}.jsonl"
        write_jsonl(path, [record])
        paths[name] = [str(path)]
    provenance = root / "training-config.yaml"
    provenance.write_text("fixed: true\n", encoding="utf-8")
    manifest = root / "training-input-manifest.json"
    write_training_input_manifest(
        manifest,
        create_training_input_manifest(
            paths, provenance_files={"training_config": str(provenance)}
        ),
    )
    return manifest


def _seal_evaluation(root: Path, *, text: str = "최종 평가 전용 문장") -> tuple[Path, Path]:
    training_manifest = _sealed_training(root)
    evaluation = root / "evaluation.jsonl"
    write_jsonl(
        evaluation,
        [
            CorpusRecord(
                id="evaluation",
                task="attack",
                text=text,
                source="fixture",
                split="test",
                attack_label="NONE",
            )
        ],
    )
    guide = root / "guide.md"
    guide.write_text("fixed labeling guide\n", encoding="utf-8")
    seal = root / "evaluation-input-manifest.json"
    write_evaluation_input_manifest(
        seal,
        create_evaluation_input_manifest(
            [evaluation],
            training_manifest_path=training_manifest,
            provenance_files={"labeling_guide": guide},
        ),
    )
    return evaluation, seal


def test_evaluation_seal_detects_post_seal_drift(
    tmp_path: Path, in_directory: None
) -> None:
    os.chdir(tmp_path)
    evaluation, seal = _seal_evaluation(Path("."))
    contract = verify_evaluation_input_manifest(seal, [evaluation])
    assert contract["sealed"] is True

    evaluation.write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content drift"):
        verify_evaluation_input_manifest(seal, [evaluation])


def test_evaluation_seal_rejects_training_fingerprint_overlap(
    tmp_path: Path, in_directory: None
) -> None:
    os.chdir(tmp_path)
    training_manifest = _sealed_training(Path("."))
    evaluation = Path("evaluation.jsonl")
    write_jsonl(
        evaluation,
        [
            CorpusRecord(
                id="evaluation",
                task="attack",
                text="attack-train의 고유 문장",
                source="fixture",
                split="test",
                attack_label="NONE",
            )
        ],
    )
    guide = Path("guide.md")
    guide.write_text("fixed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlap training"):
        create_evaluation_input_manifest(
            [evaluation],
            training_manifest_path=training_manifest,
            provenance_files={"labeling_guide": guide},
        )


def test_evaluation_seal_rejects_training_provenance_drift(
    tmp_path: Path, in_directory: None
) -> None:
    os.chdir(tmp_path)
    _, seal = _seal_evaluation(Path("."))
    Path("training-config.yaml").write_text("fixed: false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="content drift"):
        verify_evaluation_input_manifest(seal)



def test_unlabeled_pii_utterances_still_may_not_repeat(
    tmp_path: Path, in_directory: None
) -> None:
    """평가 봉인은 스팬 없는 PII 행의 겹침도 막는다 — 엄격하게 둔다.

    KDPII test 의 `네, 맞습니다.` 류 128행이 PII 학습과 겹쳐 리허설 8b 가 멈췄다. 봉인에
    예외를 여는 대신 학습 쪽에서 평가 지문을 뺀다(`train/runpod_pipeline.py` 3단계 —
    PII 병합에 평가 차단 목록). 평가셋은 §7.1.0 의 외부 증거라 건드리지 않는다.
    """
    os.chdir(tmp_path)
    training_manifest = _sealed_training(Path("."))
    evaluation = Path("evaluation.jsonl")
    write_jsonl(
        evaluation,
        [
            CorpusRecord(
                id="evaluation",
                task="pii",
                text="pii-train의 고유 문장",
                source="fixture",
                split="test",
            )
        ],
    )
    guide = Path("guide.md")
    guide.write_text("fixed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlap training"):
        create_evaluation_input_manifest(
            [evaluation],
            training_manifest_path=training_manifest,
            provenance_files={"labeling_guide": guide},
        )
