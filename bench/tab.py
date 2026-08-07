"""축 (a) 하네스 — Text Anonymization Benchmark 로 "문맥 없이는 PII 인지 알 수 없는 것" 을 잰다.

TAB(MIT, ECHR 판례)의 `identifier_type` 이 축 (a) 의 3분류를 그대로 준다:

    DIRECT    단독으로 개인을 특정한다 — 반드시 가려야 한다
    QUASI     단독으로는 불가하나 조합하면 재식별된다 — **판단이 필요한 자리**
    NO_MASK   엔티티이지만 가릴 필요가 없다 — **오탐 후보 그 자체**

test 기준 DIRECT 1,612 / QUASI 19,197 / NO_MASK 9,732 다. QUASI 와 NO_MASK 를 합친
28,929 개가 §6.4.1 축 (a) 가 L2-judge 에 넘기려던 판단 대상이다.

주석자가 둘이라 **사람 간 일치도**까지 볼 수 있다 — 자체 골드셋에는 없는 성질이다.

⚠️ 영어·유럽 법원 도메인이고 한국 고유 8종이 없다. 이 하네스는 **문맥 판단 능력**을
재는 것이지 한국어 성능 근거가 아니다(§7.5 의 유보와 같은 취지).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIRECT = "DIRECT"
QUASI = "QUASI"
NO_MASK = "NO_MASK"
# PERSON→L-CT-01, LOC→L-CT-02, ORG→L-CT-03 (§2.1 문맥 의존 3종).
ENTITY_TO_CODE = {"PERSON": "L-CT-01", "LOC": "L-CT-02", "ORG": "L-CT-03"}

Detector = Callable[[str], list[tuple[int, int]]]


@dataclass(frozen=True, slots=True)
class Mention:
    document_id: str
    annotator: str
    entity_type: str
    identifier_type: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class TabDocument:
    doc_id: str
    text: str
    mentions: list[Mention]


@dataclass(slots=True)
class TabReport:
    detector: str
    documents: int
    recall_by_identifier_type: dict[str, float]
    recall_by_entity_type: dict[str, float]
    covered: dict[str, int]
    totals: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "documents": self.documents,
            # DIRECT 재현율과 NO_MASK **오탐율**을 같은 표에 둔다 — §7.6-2.
            "recall_by_identifier_type": {
                k: round(v, 4) for k, v in sorted(self.recall_by_identifier_type.items())
            },
            "no_mask_false_positive_rate": round(
                self.recall_by_identifier_type.get(NO_MASK, 0.0), 4
            ),
            "recall_by_entity_type": {
                k: round(v, 4) for k, v in sorted(self.recall_by_entity_type.items())
            },
            "covered": dict(sorted(self.covered.items())),
            "totals": dict(sorted(self.totals.items())),
        }


def load_tab(path: str | Path, *, annotator: str | None = None) -> list[TabDocument]:
    """Read one `echr_*.json`. Without `annotator`, the first per document is used."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: TAB file must be a list of documents")
    documents: list[TabDocument] = []
    for entry in payload:
        annotations = entry.get("annotations") or {}
        if not annotations:
            continue
        chosen = annotator if annotator in annotations else next(iter(annotations))
        mentions = [
            Mention(
                document_id=str(entry.get("doc_id", "")),
                annotator=chosen,
                entity_type=str(item.get("entity_type", "")),
                identifier_type=str(item.get("identifier_type", "")),
                start=int(item["start_offset"]),
                end=int(item["end_offset"]),
            )
            for item in annotations[chosen].get("entity_mentions", [])
            if item.get("start_offset") is not None and item.get("end_offset") is not None
        ]
        documents.append(
            TabDocument(str(entry.get("doc_id", "")), str(entry.get("text", "")), mentions)
        )
    if not documents:
        raise ValueError(f"{path}: no annotated documents")
    return documents


def annotator_agreement(path: str | Path) -> dict[str, float]:
    """주석자 둘이 같은 스팬에 같은 `identifier_type` 을 붙인 비율.

    자체 골드셋에는 없는 성질이라 굳이 계산한다 — 우리 판정의 상한을 보여준다.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    agreed = total = 0
    for entry in payload:
        annotations = entry.get("annotations") or {}
        if len(annotations) < 2:
            continue
        first, second = list(annotations.values())[:2]

        def index(block: dict[str, Any]) -> dict[tuple[int, int], str]:
            return {
                (int(m["start_offset"]), int(m["end_offset"])): str(m.get("identifier_type", ""))
                for m in block.get("entity_mentions", [])
            }

        left, right = index(first), index(second)
        shared = set(left) & set(right)
        total += len(set(left) | set(right))
        agreed += sum(left[span] == right[span] for span in shared)
    return {"agreement": agreed / total if total else 0.0, "mentions": total}


def _covered(spans: Sequence[tuple[int, int]], mention: Mention) -> bool:
    """탐지 스팬이 멘션과 한 글자라도 겹치면 잡은 것으로 센다."""
    return any(start < mention.end and mention.start < end for start, end in spans)


def evaluate(documents: Sequence[TabDocument], detector: Detector, *, name: str) -> TabReport:
    if not documents:
        raise ValueError("TAB evaluation needs at least one document")
    hits: dict[str, int] = defaultdict(int)
    totals: Counter[str] = Counter()
    entity_hits: dict[str, int] = defaultdict(int)
    entity_totals: Counter[str] = Counter()

    for document in documents:
        spans = detector(document.text)
        for mention in document.mentions:
            totals[mention.identifier_type] += 1
            entity_totals[mention.entity_type] += 1
            if _covered(spans, mention):
                hits[mention.identifier_type] += 1
                entity_hits[mention.entity_type] += 1

    return TabReport(
        detector=name,
        documents=len(documents),
        recall_by_identifier_type={
            key: hits[key] / totals[key] for key in totals if totals[key]
        },
        recall_by_entity_type={
            key: entity_hits[key] / entity_totals[key]
            for key in entity_totals
            if entity_totals[key]
        },
        covered=dict(hits),
        totals=dict(totals),
    )


def garim_context_detector(policy_path: str = "config/policy.yaml") -> Detector:
    """가림 — 문맥 의존 3종(PERSON/ADDRESS/ORG_ID)은 L1 전용이라 L0 만으로는 0 이 나온다.

    그 0 이 이 하네스의 기준선이다. L1 가중치를 연결하면 여기서 증분이 보여야 한다.
    """
    from garim import Inspector

    inspector = Inspector(policy_path)
    context_codes = set(ENTITY_TO_CODE.values())

    def detector(text: str) -> list[tuple[int, int]]:
        result = inspector.check(text)
        return [f.span for f in result.leaks if f.code in context_codes]

    return detector
