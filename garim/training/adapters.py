"""Source-specific conversion into the validated pre-tokenization corpus contract."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from garim.detect.rules.checksum import (
    business_checksum_valid,
    corporation_checksum_valid,
    luhn_valid,
    rrn_checksum_valid,
    rrn_date_sex_valid,
)
from garim.training.data import CorpusRecord, CorpusSpan, Split

KDPII_LABEL_MAP = {
    "QT_RESIDENT_NUMBER": "KR_RRN",
    "QT_ALIEN_NUMBER": "KR_FRN",
    "QT_PASSPORT_NUMBER": "KR_PASSPORT",
    "QT_DRIVER_NUMBER": "KR_DL",
    "QT_HEALTH_INSURANCE_NUMBER": "KR_NHI",
    "QT_ACCOUNT_NUMBER": "KR_ACCOUNT",
    "QT_CARD_NUMBER": "CARD",
    "QT_MOBILE": "PHONE",
    "QT_PHONE": "PHONE",
    "TMI_EMAIL": "EMAIL",
    "PS_NAME": "PERSON",
    "LC_ADDRESS": "ADDRESS",
}

KLUE_NER_TAGS = {
    0: "B-DT",
    1: "I-DT",
    2: "B-LC",
    3: "I-LC",
    4: "B-OG",
    5: "I-OG",
    6: "B-PS",
    7: "I-PS",
    8: "B-QT",
    9: "I-QT",
    10: "B-TI",
    11: "I-TI",
    12: "O",
}

_ACCOUNT_TOKEN = re.compile(r"\d{2,6}(?:[- ]\d{2,6}){1,4}|\d{10,16}")
_HANGUL_NAME = re.compile(r"^[가-힣]{2,4}$")
_PERSON_SUFFIXES = ("에게서는", "에게서", "에게", "한테", "께서는", "께서", "께")
_BC_VALUE_LABELS = {
    "RRN": "KR_RRN",
    "RESIDENT_NUMBER": "KR_RRN",
    "ALIEN_NUMBER": "KR_FRN",
    "FOREIGNER_NUMBER": "KR_FRN",
    "FRN": "KR_FRN",
    "PASSPORT": "KR_PASSPORT",
    "PASSPORT_NUMBER": "KR_PASSPORT",
    "PASSPORTNUM": "KR_PASSPORT",
    "DRIVER_LICENSE": "KR_DL",
    "DRIVER_LICENSE_NUMBER": "KR_DL",
    "DRIVERLICENSENUM": "KR_DL",
    "TAXNUM": "KR_BIZNO",
    "BUSINESS_NUMBER": "KR_BIZNO",
    "CORPORATION_NUMBER": "KR_CORPNO",
    "HEALTH_INSURANCE_NUMBER": "KR_NHI",
    "BANK_ACCOUNT": "KR_ACCOUNT",
    "ACCOUNT_NUMBER": "KR_ACCOUNT",
    "ACCOUNTNUM": "KR_ACCOUNT",
    "CARDNUM": "CARD",
    "CARD_NUMBER": "CARD",
    "PHONE": "PHONE",
    "PHONE_MOBILE": "PHONE",
    "PHONE_LANDLINE": "PHONE",
    "EMAIL": "EMAIL",
}


@dataclass(slots=True)
class AdapterReport:
    source: str
    records: int = 0
    spans: Counter[str] = field(default_factory=Counter)
    ignored_labels: Counter[str] = field(default_factory=Counter)
    rejected_entities: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "records": self.records,
            "spans": dict(sorted(self.spans.items())),
            "ignored_labels": dict(sorted(self.ignored_labels.items())),
            "rejected_entities": dict(sorted(self.rejected_entities.items())),
        }


def _stable_id(source: str, *parts: object) -> str:
    raw = ":".join(str(part) for part in parts)
    return f"{source.lower()}-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _position(entity: Mapping[str, Any], text: str, form: str, cursor: int) -> tuple[int, int]:
    for start_key, end_key, inclusive in (
        ("start", "end", False),
        ("begin", "end", False),
        ("start_idx", "end_idx", True),
        ("begin_idx", "end_idx", True),
    ):
        if start_key not in entity or end_key not in entity:
            continue
        try:
            start, end = int(entity[start_key]), int(entity[end_key])
        except (TypeError, ValueError):
            continue
        if inclusive:
            end += 1
        if 0 <= start < end <= len(text) and text[start:end] == form:
            return start, end
    start = text.find(form, cursor)
    if start < 0:
        start = text.find(form)
    if start < 0:
        raise ValueError(f"entity form {form!r} does not occur in sentence")
    return start, start + len(form)


def _refine_span(label: str, text: str, start: int, end: int) -> tuple[int, int]:
    value = text[start:end]
    if label == "KR_ACCOUNT":
        match = _ACCOUNT_TOKEN.search(value)
        if match is None:
            raise ValueError("account entity contains no account-shaped token")
        return start + match.start(), start + match.end()
    if label == "PERSON":
        for suffix in _PERSON_SUFFIXES:
            if value.endswith(suffix) and len(value) - len(suffix) >= 2:
                end -= len(suffix)
                break
    return start, end


def _merge_adjacent_address_spans(text: str, spans: list[CorpusSpan]) -> list[CorpusSpan]:
    result: list[CorpusSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if (
            result
            and span.label == "ADDRESS"
            and result[-1].label == "ADDRESS"
            and not text[result[-1].end : span.start].strip()
        ):
            previous = result.pop()
            result.append(CorpusSpan(start=previous.start, end=span.end, label="ADDRESS"))
        else:
            result.append(span)
    return result


def adapt_kdpii_dialogues(
    dialogues: Sequence[Mapping[str, Any]], *, split: Split
) -> tuple[list[CorpusRecord], AdapterReport]:
    """Convert an already leakage-safe KDPII dialogue split.

    Unknown PNE labels are intentionally counted and ignored. They are never guessed into
    the 14-label project taxonomy. Account entities are narrowed to their numeric token so
    bank names do not become part of the mask.
    """
    report = AdapterReport("KDPII")
    output: list[CorpusRecord] = []
    for dialogue_index, dialogue in enumerate(dialogues):
        sentences = dialogue.get("sentence", [])
        if not isinstance(sentences, list):
            raise ValueError(f"KDPII dialogue {dialogue_index}: sentence must be a list")
        dialogue_id = str(dialogue.get("id", dialogue_index))
        for sentence_index, sentence in enumerate(sentences):
            if not isinstance(sentence, Mapping):
                raise ValueError("KDPII sentence must be an object")
            text = sentence.get("form")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("KDPII sentence.form must be a non-empty string")
            spans: list[CorpusSpan] = []
            cursor = 0
            entities = sentence.get("PNE", []) or []
            if not isinstance(entities, list):
                raise ValueError("KDPII sentence.PNE must be a list")
            for entity in entities:
                if not isinstance(entity, Mapping):
                    report.rejected_entities["not_object"] += 1
                    continue
                raw_label = entity.get("label")
                form = entity.get("form")
                if not isinstance(raw_label, str) or not isinstance(form, str) or not form:
                    report.rejected_entities["missing_label_or_form"] += 1
                    continue
                project_label = KDPII_LABEL_MAP.get(raw_label)
                if project_label is None:
                    report.ignored_labels[raw_label] += 1
                    continue
                try:
                    start, end = _position(entity, text, form, cursor)
                    start, end = _refine_span(project_label, text, start, end)
                except ValueError:
                    report.rejected_entities[raw_label] += 1
                    continue
                spans.append(CorpusSpan(start=start, end=end, label=project_label))
                report.spans[project_label] += 1
                cursor = max(cursor, end)
            spans = _merge_adjacent_address_spans(text, spans)
            try:
                record = CorpusRecord(
                    id=_stable_id("kdpii", dialogue_id, sentence_index),
                    task="pii",
                    text=text,
                    spans=spans,
                    source="KDPII",
                    split=split,
                    group_id=f"kdpii-dialogue-{dialogue_id}",
                    meta={"dialogue_id": dialogue_id, "sentence_index": sentence_index},
                )
            except ValueError as exc:
                raise ValueError(
                    f"KDPII dialogue {dialogue_id}, sentence {sentence_index}: {exc}"
                ) from exc
            output.append(record)
    report.records = len(output)
    return output, report


def _klue_ps_spans(tokens: Sequence[str], tags: Sequence[int]) -> list[CorpusSpan]:
    if len(tokens) != len(tags):
        raise ValueError("KLUE tokens and ner_tags lengths differ")
    spans: list[CorpusSpan] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        offsets.append((cursor, cursor + len(token)))
        cursor += len(token)

    index = 0
    while index < len(tokens):
        tag = KLUE_NER_TAGS.get(int(tags[index]))
        if tag is None:
            raise ValueError(f"unknown KLUE NER tag id: {tags[index]}")
        if tag != "B-PS":
            index += 1
            continue
        end_index = index + 1
        while end_index < len(tokens) and KLUE_NER_TAGS[int(tags[end_index])] == "I-PS":
            end_index += 1
        value = "".join(tokens[index:end_index])
        if _HANGUL_NAME.fullmatch(value):
            spans.append(
                CorpusSpan(
                    start=offsets[index][0],
                    end=offsets[end_index - 1][1],
                    label="PERSON",
                )
            )
        index = end_index
    return spans


def adapt_klue_ner(
    rows: Iterable[Mapping[str, Any]], *, split: Split, source_name: str
) -> tuple[list[CorpusRecord], AdapterReport]:
    report = AdapterReport(source_name)
    output: list[CorpusRecord] = []
    for index, row in enumerate(rows):
        tokens = row.get("tokens")
        tags = row.get("ner_tags")
        if not isinstance(tokens, list) or not isinstance(tags, list):
            raise ValueError("KLUE row must contain tokens and ner_tags lists")
        text = "".join(str(token) for token in tokens)
        spans = _klue_ps_spans(tokens, tags)
        report.spans["PERSON"] += len(spans)
        output.append(
            CorpusRecord(
                id=f"klue-ner-{split}-{index:06d}",
                task="pii",
                text=text,
                spans=spans,
                source=source_name,
                split=split,
                group_id=f"klue-ner-{split}-{index:06d}",
                meta={"filter": "PS Hangul 2-4 characters"},
            )
        )
    report.records = len(output)
    return output, report


def adapt_polyguard_mix(rows: Iterable[Mapping[str, Any]]) -> Iterator[CorpusRecord]:
    """Yield only Korean prompts; source harm labels are not project attack labels."""
    for index, row in enumerate(rows):
        metadata = row.get("metadata")
        language = metadata.get("language") if isinstance(metadata, Mapping) else None
        if language != "ko":
            continue
        text = row.get("prompt")
        if not isinstance(text, str) or not text.strip():
            continue
        source_id = metadata.get("source") if isinstance(metadata, Mapping) else None
        yield CorpusRecord(
            id=_stable_id("polyguardmix", source_id, index, text),
            task="attack",
            text=text,
            source="PolyGuardMix",
            split="train",
            group_id=f"polyguardmix-{source_id or index}",
            meta={
                "source_id": source_id,
                "original_prompt_harm_label": row.get("prompt_harm_label"),
                "requires_teacher_label": True,
            },
        )


def adapt_polyguard_prompts(rows: Iterable[Mapping[str, Any]]) -> Iterator[CorpusRecord]:
    for index, row in enumerate(rows):
        if row.get("language") != "ko":
            continue
        text = row.get("prompt")
        if not isinstance(text, str) or not text.strip():
            continue
        source_id = row.get("id", index)
        yield CorpusRecord(
            id=f"polyguardprompts-{source_id}",
            task="attack",
            text=text,
            source="PolyGuardPrompts",
            split="test",
            group_id=f"polyguardprompts-{source_id}",
            meta={
                "source_id": source_id,
                "requires_manual_label": True,
                "original_prompt_label": row.get("prompt_label"),
                "adversarial": row.get("adversarial"),
            },
        )


def adapt_deepset_translation_queue(
    rows: Iterable[Mapping[str, Any]], *, split: Split
) -> Iterator[CorpusRecord]:
    """Create a split-preserving translation queue; English rows are not trainable yet."""
    for index, row in enumerate(rows):
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        yield CorpusRecord(
            id=f"deepset-{split}-{index:04d}",
            task="attack",
            text=text,
            source="deepset-prompt-injections",
            split=split,
            group_id=f"deepset-{split}-{index:04d}",
            meta={
                "original_language": "en",
                "original_binary_label": row.get("label"),
                "requires_translation": True,
                "requires_teacher_label": split == "train",
                "requires_manual_label": split != "train",
            },
        )


def _bc_value_allowed(label: str, value: str) -> bool:
    compact = re.sub(r"\s", "", value)
    if label == "KR_RRN":
        return rrn_checksum_valid(compact)
    if label == "KR_FRN":
        digits = re.sub(r"\D", "", compact)
        return len(digits) == 13 and digits[6:7] in "5678" and rrn_date_sex_valid(digits)
    if label == "KR_BIZNO":
        return business_checksum_valid(compact)
    if label == "KR_CORPNO":
        return corporation_checksum_valid(compact)
    if label == "CARD":
        return luhn_valid(compact)
    patterns = {
        "KR_PASSPORT": r"(?:[MSROmsro]\d{8}|[A-Za-z]{2}\d{7})",
        "KR_DL": r"(?:1[1-9]|2[0-8])[- ]\d{2}[- ]\d{6}[- ]\d{2}",
        "KR_NHI": r"[12]\d{9}",
        "KR_ACCOUNT": r"\d{2,6}(?:[- ]\d{2,6}){1,4}",
        "PHONE": (
            r"(?:(?:\+82[- ]?1[016789]|01[016789])[- ]?\d{3,4}[- ]?\d{4}"
            r"|0(?:2|70|[3-6][1-5])[- ]?\d{3,4}[- ]?\d{4})"
        ),
        "EMAIL": r"[^@\s]+@[^@\s]+\.[^@\s]+",
    }
    return bool(re.fullmatch(patterns[label], compact))


def extract_bc_value_pools(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, list[str]], AdapterReport]:
    """Extract only reviewed value fields; never import bc-pii-ko translated context."""
    report = AdapterReport("bc-pii-ko")
    pools: dict[str, set[str]] = {}
    for row in rows:
        masks = row.get("privacy_mask", []) or []
        if not isinstance(masks, list):
            report.rejected_entities["privacy_mask_not_list"] += 1
            continue
        for mask in masks:
            if not isinstance(mask, Mapping):
                continue
            raw_label, value = mask.get("label"), mask.get("value")
            if not isinstance(raw_label, str) or not isinstance(value, str):
                continue
            label = _BC_VALUE_LABELS.get(raw_label.upper())
            if label is None:
                report.ignored_labels[raw_label] += 1
                continue
            if not _bc_value_allowed(label, value):
                report.rejected_entities[label] += 1
                continue
            pools.setdefault(label, set()).add(value)
    normalized = {label: sorted(values) for label, values in pools.items()}
    report.spans.update({label: len(values) for label, values in normalized.items()})
    return normalized, report


def adapt_bc_augmented_negatives(
    rows: Iterable[Mapping[str, Any]],
) -> list[CorpusRecord]:
    output: list[CorpusRecord] = []
    for index, row in enumerate(rows):
        if row.get("provenance") != "augmented-negative":
            continue
        if row.get("privacy_mask") not in ([], None):
            raise ValueError("bc-pii-ko augmented-negative row unexpectedly contains spans")
        text = row.get("source_text")
        if not isinstance(text, str) or not text.strip():
            continue
        uid = str(row.get("uid", index))
        output.append(
            CorpusRecord(
                id=f"bc-pii-negative-{uid}",
                task="pii",
                text=text,
                source="bc-pii-ko-augmented-negative",
                split="test",
                group_id=f"bc-pii-negative-{uid}",
                meta={"external_fpr_only": True, "provenance": "augmented-negative"},
            )
        )
    return output
