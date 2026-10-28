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
        digits = positive.text[span.start : span.end]
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
        return json.dumps(
            {
                "id": record_id,
                "task": "attack",
                "text": text,
                "source": "fixture",
                "split": "train",
            },
            ensure_ascii=False,
        )

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
        "\n".join(
            [
                row("same", "변하지 않은 행"),
                row("edited", "R2 에서 바뀐 텍스트"),
                row("new", "새 행"),
            ]
        )
        + "\n",
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


def test_data_audit_stops_the_r1_generator_and_passes_r2(attack: list[CorpusRecord]) -> None:
    """R1 을 학습까지 보낸 세 결함을 감사가 잡아야 한다(RESULTS_L1 §5-③)."""
    from garim.training.data_audit import DataAuditError, audit_attack_synthetic
    from garim.training.synthetic import generate_attack_corpus

    with pytest.raises(DataAuditError) as caught:
        audit_attack_synthetic(generate_attack_corpus())
    message = str(caught.value)
    assert "학습 쪽에도 있다" in message and "라벨 둘 이상" in message and "부족" in message
    report = audit_attack_synthetic(attack)
    assert report["test_bases_seen_in_training"] == 0 and report["label_conflicts"] == 0


def test_consensus_audit_stops_when_generator_benign_is_dropped_wholesale() -> None:
    from garim.training.data_audit import DataAuditError, audit_generator_consensus

    gold = {f"r{i}": "NONE" for i in range(10)}
    families = {f"r{i}": "fam-a" if i < 5 else "fam-b" for i in range(10)}
    kept = [{"id": f"r{i}", "status": "accepted", "label": "NONE"} for i in range(10)]
    assert audit_generator_consensus(kept, gold, families)["benign_acceptance"] == 1.0
    r1_like = [
        {"id": f"r{i}", "status": "accepted", "label": "NONE"}
        if i >= 5
        else {"id": f"r{i}", "status": "review", "label": None, "reason": "no_consensus"}
        for i in range(10)
    ]
    with pytest.raises(DataAuditError, match="fam-a"):
        audit_generator_consensus(r1_like, gold, families)


def test_reference_numbers_do_not_reveal_the_label(attack: list[CorpusRecord]) -> None:
    """R1·초기 R2 는 꼬리말 번호가 생성 순서라 공격 0~599 · 정상 600~ 로 갈렸다."""
    from garim.normalize.chain import normalize

    numbers: dict[bool, list[int]] = defaultdict(list)
    for record in attack:
        found = re.search(r"참고 번호 (\d{4})", normalize(record.text).norm.text)
        if found:
            numbers[record.attack_label == "NONE"].append(int(found.group(1)))
    benign, hostile = numbers[True], numbers[False]
    # 번호 하나로 공격과 정상을 가르는 확률(AUC)이 동전 던지기와 같아야 한다.
    wins = sum((b > h) + 0.5 * (b == h) for b in benign[::7] for h in hostile)
    auc = wins / (len(benign[::7]) * len(hostile))
    assert abs(auc - 0.5) < 0.05


def test_benign_rows_carry_every_t4_benign_hiding_except_b64(attack: list[CorpusRecord]) -> None:
    """정상 학습문에 없는 은닉으로 정상 FPR(O2)을 재면 모델과 무관하게 통과할 수 없다."""
    hidings = Counter(r.meta["hiding"] for r in attack if r.attack_label == "NONE")
    for technique in (
        "zwsp",
        "jamo",
        "space_break",
        "tensify",
        "homoglyph",
        "hanja",
        "chosung",
        "space_join",
    ):
        assert hidings[technique] > 0, technique
    assert hidings["b64"] == 0


def test_homoglyph_marker_does_not_track_the_label(attack: list[CorpusRecord]) -> None:
    """정상 한국어 행에만 라틴 접두를 심었더니 그것이 NONE 의 지름길이 됐다(재검토)."""
    from garim.normalize.chain import normalize

    marker = re.compile(r"^(xlsx|html|xml|css) 자료:", re.I)
    by_label = Counter(
        (r.attack_label == "NONE", bool(marker.search(normalize(r.text).norm.text)))
        for r in attack
        if r.meta["hiding"] == "homoglyph"
    )
    assert by_label[(False, True)] and by_label[(False, False)]  # 공격에도 접두 있음·없음
    assert by_label[(True, False)]  # 정상에도 접두 없는 동형 문자
    # 한글 본문 위의 동형 문자(= `t4-r2-test` 동형 문자 셀의 모양)도 두 라벨에 다 있다.
    hangul = Counter(
        r.attack_label == "NONE"
        for r in attack
        if r.meta["hiding"] == "homoglyph" and not r.romanized_target
    )
    assert hangul[True] and hangul[False]
    # T4 정상 셀의 접두(`CSV 첨부:` 등)는 학습에 나오지 않는다.
    assert not any("첨부:" in normalize(r.text).norm.text for r in attack if r.split != "test")


def test_one_attack_family_per_class_is_pinned_to_validation(attack: list[CorpusRecord]) -> None:
    from garim.training.split import component_validation_splits

    pinned = {
        (r.attack_label, r.template_id)
        for r in attack
        if r.attack_label != "NONE" and r.split == "validation"
    }
    assert {label for label, _ in pinned} == {"INJECTION", "JAILBREAK", "SYSTEM_LEAK"}
    assert all(family.endswith("-t2") for _, family in pinned)
    pool = [r for r in attack if r.split != "test"]
    splits = component_validation_splits(pool, 10)
    assert all(splits[r.id] == r.split for r in pool)  # 7단계가 다시 나누지 않는다


def test_shipping_contexts_reach_training_and_some_numbers_lead_with_zero(
    pii: list[CorpusRecord],
) -> None:
    shipping = [r for r in pii if r.template_id.startswith("pii-num-ship-")]
    assert shipping and all(r.split == "train" for r in shipping)
    assert any(
        re.search(r"(?<!\d)0\d{7,}", r.text)
        for r in pii
        if r.meta["type"] == "numeric_context_negative"
    )
    numbers = [
        token
        for r in pii
        if r.meta["type"] == "numeric_context_negative"
        for token in re.findall(r"\d[\d-]{6,}\d", r.text)
    ]
    assert numbers and not any(token.startswith("010") for token in numbers)
