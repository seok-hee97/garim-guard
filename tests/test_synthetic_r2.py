"""R2 합성 — PREREG_L1_R2 C1 · C2 · C4 와 6a 재사용 규칙."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from garim.training.benign_bank import BENIGN_FAMILIES
from garim.training.data import CorpusRecord
from garim.training.pii_negatives_r2 import _PLAIN, generate_pii_negatives_r2
from garim.training.synthetic_r2 import generate_attack_corpus_r2
from garim.training.t4 import T4_HIDINGS, generate_t4_benign


@pytest.fixture(scope="module")
def attack() -> list[CorpusRecord]:
    return generate_attack_corpus_r2()


@pytest.fixture(scope="module")
def pii() -> list[CorpusRecord]:
    return generate_pii_negatives_r2()


def test_benign_bank_meets_the_registered_composition() -> None:
    kinds = Counter(kind for _, kind, _ in BENIGN_FAMILIES)
    assert len(BENIGN_FAMILIES) >= 120
    assert kinds["trigger"] >= 60 and kinds["general"] >= 30 and kinds["design"] >= 30
    texts = [text for *_, family_texts in BENIGN_FAMILIES for text in family_texts]
    assert len(texts) == len(set(texts))


def test_every_family_lives_in_exactly_one_split(attack: list[CorpusRecord]) -> None:
    """R1 의 `template_id` 는 인덱스 나머지라 같은 문장이 train·test 에 다 있었다(§5-③)."""
    splits: dict[str, set[str]] = defaultdict(set)
    for record in attack:
        assert record.template_id == record.meta["family"]
        splits[record.template_id].add(record.split)
    assert all(len(owned) == 1 for owned in splits.values())


def test_attack_families_never_reach_the_holdout(attack: list[CorpusRecord]) -> None:
    """R2 는 공격 문장을 새로 쓰지 않으므로 공격 홀드아웃이 없다(PREREG §8)."""
    holdout = [record for record in attack if record.split == "test"]
    assert holdout and all(record.attack_label == "NONE" for record in holdout)
    assert len({record.template_id for record in holdout}) >= 20


def test_no_text_carries_two_labels(attack: list[CorpusRecord]) -> None:
    """R1 은 로마자·존댓말 고정 문장을 공격 세 라벨에 20행씩 넣었다."""
    labels: dict[str, set[str]] = defaultdict(set)
    for record in attack:
        labels[re.sub(r" 참고 번호 \d{4}\.$", "", record.text)].add(str(record.attack_label))
    assert all(len(owned) == 1 for owned in labels.values())


def test_surface_transforms_reach_benign_rows_too(attack: list[CorpusRecord]) -> None:
    benign = [record for record in attack if record.attack_label == "NONE"]
    assert any(record.romanized_target == 1.0 for record in benign)
    assert any(record.honorific_target == 1.0 for record in benign)


def test_generation_is_deterministic(attack: list[CorpusRecord], pii: list[CorpusRecord]) -> None:
    assert [r.model_dump_json() for r in generate_attack_corpus_r2()] == [
        r.model_dump_json() for r in attack
    ]
    assert [r.model_dump_json() for r in generate_pii_negatives_r2()] == [
        r.model_dump_json() for r in pii
    ]


def test_benign_t4_cells_apply_every_hiding(attack: list[CorpusRecord]) -> None:
    holdout = [record for record in attack if record.split == "test"]
    rows = generate_t4_benign(holdout)
    assert len(rows) == 700
    assert Counter(row.meta["hiding"] for row in rows) == {
        technique: 100 for _, technique in T4_HIDINGS
    }
    assert all(row.meta["family"].startswith("benign-") for row in rows)
    with pytest.raises(ValueError):
        generate_t4_benign([record for record in attack if record.split != "test"][:5])


def test_account_pairs_share_digits_and_split(pii: list[CorpusRecord]) -> None:
    pairs: dict[str, list[CorpusRecord]] = defaultdict(list)
    for record in pii:
        if record.pair_id is not None:
            pairs[record.pair_id].append(record)
    assert len(pairs) == 1_000
    for members in pairs.values():
        positive = next(r for r in members if r.meta["type"] == "account_pair_positive")
        negative = next(r for r in members if r.meta["type"] == "numeric_context_negative")
        (span,) = positive.spans
        digits = positive.text[span.start:span.end]
        assert span.label == "KR_ACCOUNT" and digits.isdigit() and 10 <= len(digits) <= 14
        assert digits in negative.text and not negative.spans
        assert positive.split == negative.split


def test_negative_categories_carry_no_spans_and_hold_out_families(
    pii: list[CorpusRecord],
) -> None:
    counts = Counter(record.meta["type"] for record in pii)
    assert counts == {
        "numeric_context_negative": 4_000,
        "account_pair_positive": 1_000,
        "obfuscated_negative": 3_000,
    }
    assert all(not r.spans for r in pii if r.meta["type"] != "account_pair_positive")
    held_out = {r.template_id: r for r in pii if r.split == "test"}.values()
    test_families = Counter(record.meta["type"] for record in held_out)
    assert test_families["numeric_context_negative"] >= 5
    assert test_families["obfuscated_negative"] >= 5
    assert test_families["account_pair_positive"] >= 1


def test_pii_plain_sentences_do_not_overlap_the_attack_benign_bank() -> None:
    """겹치면 `t4-r2-test` 의 정상문이 PII 학습에서 보인다."""
    bank = {text for *_, family_texts in BENIGN_FAMILIES for text in family_texts}
    assert not bank & set(_PLAIN)


def test_r1_teacher_rows_are_reused_only_when_id_and_text_match(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path("train").resolve()))
    from runpod_pipeline import TEACHERS, reuse_r1_teachers

    def row(record_id: str, text: str) -> str:
        return json.dumps({"id": record_id, "task": "attack", "text": text, "source": "fixture",
                           "split": "train"}, ensure_ascii=False)

    source = tmp_path / "r1"
    source.mkdir()
    (source / "attack-teacher-input.jsonl").write_text(
        "\n".join([row("same", "변하지 않은 행"), row("edited", "R1 의 텍스트")]) + "\n",
        encoding="utf-8",
    )
    for name in TEACHERS:
        (source / f"teacher-{name}.jsonl").write_text(
            "".join(json.dumps({"id": key, "teacher": name}) + "\n" for key in ("same", "edited")),
            encoding="utf-8",
        )
    current = tmp_path / "r2-input.jsonl"
    current.write_text(
        "\n".join([row("same", "변하지 않은 행"), row("edited", "R2 에서 바뀐 텍스트"),
                   row("new", "새 행")]) + "\n",
        encoding="utf-8",
    )
    outputs = {name: tmp_path / f"teacher-{name}-r2.jsonl" for name in TEACHERS}
    reuse_r1_teachers(current, source, outputs)
    for path in outputs.values():
        kept = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]
        assert kept == ["same"]
    digest = hashlib.sha256(outputs["kanana"].read_bytes()).hexdigest()
    reuse_r1_teachers(current, source, outputs)  # 이미 있으면 건드리지 않는다
    assert hashlib.sha256(outputs["kanana"].read_bytes()).hexdigest() == digest


def test_cluster_bootstrap_counts_families_not_rows() -> None:
    from bench.r2_eval import cluster_bootstrap

    # 한 family 가 행 100개를 가져도 표본은 family 둘이다.
    units = [("a", 1.0, 1.0)] * 100 + [("b", 0.0, 1.0)] * 100
    summary = cluster_bootstrap(units)
    assert summary["value"] == 0.5 and summary["families"] == 2
    assert summary["ci95"][0] == 0.0 and summary["ci95"][1] == 1.0
    assert cluster_bootstrap(units) == summary  # seed 고정


def test_negatives_never_trip_the_rule_layer(pii: list[CorpusRecord]) -> None:
    """L0 가 잡는 음성은 L1 과 무관한 제품 경로 오탐이라 O3 를 판정 불가로 만든다."""
    from garim.detect.rules import detect_common_pii, detect_kr_pii

    for record in pii:
        if not record.spans:
            assert not detect_kr_pii(record.text) + detect_common_pii(record.text), record.id
