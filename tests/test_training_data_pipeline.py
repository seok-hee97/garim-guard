from __future__ import annotations

from collections import Counter

import pytest

from garim.training.adapters import (
    adapt_bc_augmented_negatives,
    adapt_kdpii_dialogues,
    adapt_klue_ner,
    extract_bc_value_pools,
)
from garim.training.data import CorpusRecord, CorpusSpan, validate_corpus
from garim.training.dataset import encode_corpus_record
from garim.training.export import PII_LABELS
from garim.training.readiness import (
    validate_sealed_evaluation_corpus,
    validate_training_corpora,
)
from garim.training.synthetic import (
    _business_check_digit,
    generate_attack_corpus,
    generate_pii_corpus,
)


def test_kdpii_adapter_narrows_account_and_does_not_guess_unknown_labels() -> None:
    dialogues = [
        {
            "id": "d1",
            "sentence": [
                {
                    "form": "가림은행 123-4567-8901 계좌로 입금해 주세요",
                    "PNE": [
                        {
                            "form": "가림은행 123-4567-8901",
                            "label": "QT_ACCOUNT_NUMBER",
                        },
                        {"form": "가림은행", "label": "OG_COMPANY"},
                    ],
                }
            ],
        }
    ]
    records, report = adapt_kdpii_dialogues(dialogues, split="train")
    assert records[0].text[records[0].spans[0].start : records[0].spans[0].end] == ("123-4567-8901")
    assert report.ignored_labels == Counter({"OG_COMPANY": 1})


def test_kdpii_spans_never_carry_edge_whitespace_into_training_windows() -> None:
    """★ 회귀 — 원천 span 이 앞 공백을 물어 20토큰짜리 행이 "512토큰 창에 안 들어간다" 로
    RunPod 첫 학습을 멈췄다. 토큰은 공백을 덮지 않는다.
    """
    dialogues = [
        {
            "id": "d2",
            "sentence": [
                {
                    "form": " 3762 743966 38584 카드로 결제해 주세요",
                    "PNE": [{"form": " 3762 743966 38584", "label": "QT_CARD_NUMBER"}],
                }
            ],
        }
    ]
    records, _ = adapt_kdpii_dialogues(dialogues, split="train")
    span = records[0].spans[0]
    assert records[0].text[span.start : span.end] == "3762 743966 38584"


def test_klue_adapter_keeps_only_hangul_ps_names_of_two_to_four_characters() -> None:
    rows = [
        {
            "tokens": ["김", "민", "준", "과", " ", "A", "씨"],
            "ner_tags": [6, 7, 7, 12, 12, 6, 12],
        }
    ]
    records, report = adapt_klue_ner(rows, split="train", source_name="KLUE-NER")
    assert [(span.label, records[0].text[span.start : span.end]) for span in records[0].spans] == [
        ("PERSON", "김민준")
    ]
    assert report.spans["PERSON"] == 1


def test_corpus_validator_rejects_template_leakage() -> None:
    records = [
        CorpusRecord(
            id="one",
            task="pii",
            text="홍길동",
            spans=[CorpusSpan(start=0, end=3, label="PERSON")],
            source="fixture",
            split="train",
            template_id="same",
        ),
        CorpusRecord(
            id="two",
            task="pii",
            text="김가림",
            spans=[CorpusSpan(start=0, end=3, label="PERSON")],
            source="fixture",
            split="test",
            template_id="same",
        ),
    ]
    with pytest.raises(ValueError, match="template"):
        validate_corpus(records)


def test_bc_pii_context_is_not_imported_but_values_and_external_negatives_are() -> None:
    prefix = "123456789"
    business = prefix + _business_check_digit(prefix)
    rows = [
        {
            "source_text": f"세금 식별번호는 {business}",
            "privacy_mask": [{"label": "TAXNUM", "value": business}],
            "provenance": "augmented-values",
        },
        {
            "uid": "neg-1",
            "source_text": "주문번호 ORD-1234를 확인해 주세요",
            "privacy_mask": [],
            "provenance": "augmented-negative",
        },
    ]
    pools, _ = extract_bc_value_pools(rows)
    assert pools == {"KR_BIZNO": [business]}
    negatives = adapt_bc_augmented_negatives(rows)
    assert len(negatives) == 1
    assert negatives[0].spans == []
    assert "세금 식별번호" not in negatives[0].text


def test_synthetic_generators_hold_splits_and_design_class_counts() -> None:
    pii = generate_pii_corpus(count=1_000)
    assert len(pii) == 1_000
    assert len({record.template_id for record in pii}) >= 750
    assert Counter(record.meta["doc_shape"] for record in pii) == {
        "prose": 550,
        "unknown": 50,
        "code": 150,
        "structured": 150,
        "log": 50,
        "mixed": 50,
    }
    validate_corpus(pii)

    attack = generate_attack_corpus()
    assert Counter(record.attack_label for record in attack) == {
        "NONE": 1_200,
        "INJECTION": 200,
        "JAILBREAK": 200,
        "SYSTEM_LEAK": 200,
    }
    assert set(record.split for record in attack) == {"train", "validation", "test"}
    validate_corpus(attack)


def test_joint_encoder_uses_normalized_offsets_and_masks_missing_task() -> None:
    class CharacterTokenizer:
        def __call__(self, text, **kwargs):  # noqa: ANN001
            del kwargs
            return {
                "input_ids": [[101, *range(1, len(text) + 1), 102]],
                "attention_mask": [[1] * (len(text) + 2)],
                "offset_mapping": [[(0, 0), *[(i, i + 1) for i in range(len(text))], (0, 0)]],
                "overflow_to_sample_mapping": [0],
            }

    pii = CorpusRecord(
        id="pii",
        task="pii",
        text="홍길동에게",
        spans=[CorpusSpan(start=0, end=3, label="PERSON")],
        source="fixture",
        split="train",
    )
    encoded = encode_corpus_record(CharacterTokenizer(), pii)[0]
    assert encoded["pii_labels"][1:4] == [
        PII_LABELS.index("B-PERSON"),
        PII_LABELS.index("I-PERSON"),
        PII_LABELS.index("I-PERSON"),
    ]
    assert encoded["attack_labels"] == -100

    attack = CorpusRecord(
        id="attack",
        task="attack",
        text="이전 지시 무시",
        source="fixture",
        split="train",
        attack_label="INJECTION",
    )
    assert encoded["pii_eval_labels"] == encoded["pii_labels"]

    encoded_attack = encode_corpus_record(CharacterTokenizer(), attack)[0]
    assert set(encoded_attack["pii_labels"]) == {-100}
    assert encoded_attack["attack_labels"] == 1
    # ★ 손실은 마스킹하되 지표는 본다. 평가 텐서가 실토큰을 `O` 로 두지 않으면
    #   공격 텍스트 위의 PII 오탐이 precision 에 한 건도 잡히지 않는다(§7.1.1).
    offsets = [(0, 0), *[(i, i + 1) for i in range(len(attack.text))], (0, 0)]
    assert encoded_attack["pii_eval_labels"] == [
        -100 if start == end else PII_LABELS.index("O") for start, end in offsets
    ]


def test_an_entity_cut_by_a_window_edge_is_ignored_not_taught_as_o() -> None:
    """★ 전수 검토 — 창 경계에 걸린 개체의 창 안 토큰이 `O` 로 학습됐다."""

    class WindowTokenizer:
        def __call__(self, text, **kwargs):  # noqa: ANN001
            del kwargs
            windows = [(0, 6), (4, len(text))]  # 개체 3..7 이 첫 창 끝에 걸린다
            return {
                "input_ids": [[101, *range(start, end), 102] for start, end in windows],
                "attention_mask": [[1] * (end - start + 2) for start, end in windows],
                "offset_mapping": [
                    [(0, 0), *[(i, i + 1) for i in range(start, end)], (0, 0)]
                    for start, end in windows
                ],
                "overflow_to_sample_mapping": [0, 0],
            }

    record = CorpusRecord(
        id="cut",
        task="pii",
        text="연락처 홍길동님 확인",
        spans=[CorpusSpan(start=4, end=7, label="PERSON")],
        source="fixture",
        split="train",
    )
    first, second = encode_corpus_record(WindowTokenizer(), record)
    # 첫 창(0..6)에서 개체 글자 4·5 는 -100, 개체 밖 글자는 O.
    assert first["pii_labels"][1:7] == [PII_LABELS.index("O")] * 4 + [-100, -100]
    # 둘째 창(4..)은 개체 전체를 담아 B · I · I 로 배운다.
    assert second["pii_labels"][1:4] == [
        PII_LABELS.index("B-PERSON"),
        PII_LABELS.index("I-PERSON"),
        PII_LABELS.index("I-PERSON"),
    ]


def test_training_readiness_rejects_unresolved_manual_labels() -> None:
    pii_train = CorpusRecord(
        id="pii-train",
        task="pii",
        text="정상 문장",
        source="fixture",
        split="train",
    )
    pii_validation = pii_train.model_copy(
        update={"id": "pii-validation", "text": "다른 문장", "split": "validation"}
    )
    attack_train = CorpusRecord(
        id="attack-train",
        task="attack",
        text="검토가 남은 문장",
        source="fixture",
        split="train",
        attack_label="NONE",
        meta={"requires_manual_label": True},
    )
    attack_validation = attack_train.model_copy(
        update={
            "id": "attack-validation",
            "text": "검토 완료 문장",
            "split": "validation",
            "meta": {},
        }
    )
    with pytest.raises(ValueError, match="requires_manual_label"):
        validate_training_corpora(
            [pii_train], [pii_validation], [attack_train], [attack_validation]
        )


def test_final_evaluation_rejects_unlabeled_attack_rows() -> None:
    record = CorpusRecord(
        id="eval",
        task="attack",
        text="수동 판정 전",
        source="fixture",
        split="test",
    )
    with pytest.raises(ValueError, match="no manual gold label"):
        validate_sealed_evaluation_corpus([record])


@pytest.mark.parametrize("positive_first", [True, False])
def test_target_bearing_pii_duplicate_never_crosses_splits(positive_first: bool) -> None:
    text = "홍길동 고객입니다"
    positive = CorpusRecord(
        id="positive",
        task="pii",
        text=text,
        source="fixture",
        split="train",
        spans=[CorpusSpan(start=0, end=3, label="PERSON")],
    )
    negative = CorpusRecord(
        id="negative",
        task="pii",
        text=text,
        source="fixture",
        split="validation",
    )
    rows = [positive, negative] if positive_first else [negative, positive]
    with pytest.raises(ValueError, match="fingerprint crosses"):
        validate_corpus(rows)
