"""축 (a) TAB 하네스와 §7.1.3 지연 하네스."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.latency import BUDGET_P50_MS, BUDGET_P95_MS, environment, measure
from bench.tab import (
    annotator_agreement,
    evaluate,
    garim_context_detector,
    load_tab,
)
from garim import Inspector, Segment

TAB_FIXTURE = [
    {
        "doc_id": "d1",
        "text": "Mr Smith lives in Oslo and works for Acme in 2019.",
        "annotations": {
            "ann1": {
                "entity_mentions": [
                    {
                        "entity_type": "PERSON",
                        "identifier_type": "DIRECT",
                        "start_offset": 3,
                        "end_offset": 8,
                    },
                    {
                        "entity_type": "LOC",
                        "identifier_type": "QUASI",
                        "start_offset": 18,
                        "end_offset": 22,
                    },
                    {
                        "entity_type": "ORG",
                        "identifier_type": "NO_MASK",
                        "start_offset": 37,
                        "end_offset": 41,
                    },
                ]
            },
            "ann2": {
                "entity_mentions": [
                    {
                        "entity_type": "PERSON",
                        "identifier_type": "DIRECT",
                        "start_offset": 3,
                        "end_offset": 8,
                    },
                    # 두 번째 주석자는 같은 스팬을 다르게 본다 — 일치도의 분모다.
                    {
                        "entity_type": "LOC",
                        "identifier_type": "NO_MASK",
                        "start_offset": 18,
                        "end_offset": 22,
                    },
                ]
            },
        },
    }
]


@pytest.fixture
def tab_file(tmp_path: Path) -> Path:
    path = tmp_path / "echr_test.json"
    path.write_text(json.dumps(TAB_FIXTURE), encoding="utf-8")
    return path


def test_loader_reads_the_three_identifier_types(tab_file: Path) -> None:
    (document,) = load_tab(tab_file)
    assert {m.identifier_type for m in document.mentions} == {"DIRECT", "QUASI", "NO_MASK"}


def test_loader_honours_the_requested_annotator(tab_file: Path) -> None:
    (second,) = load_tab(tab_file, annotator="ann2")
    assert len(second.mentions) == 2


def test_no_mask_recall_is_reported_as_a_false_positive_rate(tab_file: Path) -> None:
    """NO_MASK 를 '잡는' 것은 성과가 아니라 오탐이다 — 표에서 그렇게 읽히게 둔다."""
    documents = load_tab(tab_file)
    report = evaluate(documents, lambda text: [(0, len(text))], name="flag-everything")
    payload = report.as_dict()
    assert payload["recall_by_identifier_type"]["DIRECT"] == 1.0
    assert payload["no_mask_false_positive_rate"] == 1.0


def test_partial_overlap_counts_as_covered(tab_file: Path) -> None:
    documents = load_tab(tab_file)
    report = evaluate(documents, lambda text: [(4, 6)], name="partial")
    assert report.recall_by_identifier_type["DIRECT"] == 1.0
    assert report.recall_by_identifier_type["QUASI"] == 0.0


def test_annotator_agreement_uses_the_union_as_denominator(tab_file: Path) -> None:
    result = annotator_agreement(tab_file)
    # 공유 스팬 2개 중 1개 일치, 합집합 3개.
    assert result["mentions"] == 3
    assert result["agreement"] == pytest.approx(1 / 3)


def test_l0_scores_zero_because_context_codes_are_l1_only(tab_file: Path) -> None:
    """0.0 이 이 하네스의 기준선이다 — L1 을 붙였을 때의 증분이 여기서 보인다."""
    report = evaluate(load_tab(tab_file), garim_context_detector(), name="garim-l0")
    assert set(report.recall_by_identifier_type.values()) == {0.0}


def test_degraded_documents_are_counted_not_hidden(tab_file: Path) -> None:
    """L1 이 시간 초과로 빠진 문서의 0 을 판별력으로 읽지 않게 따로 센다."""
    from collections import Counter

    degraded: Counter[str] = Counter()
    evaluate(load_tab(tab_file), garim_context_detector(degraded=degraded), name="garim-l0")
    assert degraded["documents"] == 0


def test_empty_document_set_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one document"):
        evaluate([], lambda text: [], name="empty")


def test_latency_report_declares_its_environment() -> None:
    """§7.6-5 — 사양 없는 지연 수치는 보고하지 않는다."""
    info = environment()
    assert info["platform"] and info["machine"] and info["python"]
    assert "l1_backend" in info


def test_latency_point_compares_against_the_multi_segment_budget() -> None:
    inspector = Inspector("config/policy.yaml")
    point = measure(
        inspector,
        [Segment("연락처 010-1234-5678 입니다", origin="user") for _ in range(3)],
        label="3seg",
        runs=5,
        warmup=1,
    )
    assert point.segments == 3
    assert point.p50_ms > 0
    assert point.p95_ms >= point.p50_ms
    # L0 만 도는 P0 프로파일은 §7.1.3 예산에 한참 못 미쳐야 한다.
    assert point.within_budget
    assert (BUDGET_P50_MS, BUDGET_P95_MS) == (300.0, 700.0)
