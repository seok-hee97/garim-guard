"""Deterministic group splitting that prevents normalized identifier leakage."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

_T = TypeVar("_T")
_SPACE = re.compile(r"\s+")
_IDENTIFIER_SEPARATORS = re.compile(r"[\s\-_.:/]+")

KDPII_IDENTIFIER_LABELS = frozenset(
    {
        "PS_ID",
        "QT_ACCOUNT_NUMBER",
        "QT_ALIEN_NUMBER",
        "QT_CARD_NUMBER",
        "QT_DRIVER_NUMBER",
        "QT_IP",
        "QT_MOBILE",
        "QT_PASSPORT_NUMBER",
        "QT_PHONE",
        "QT_PLATE_NUMBER",
        "QT_RESIDENT_NUMBER",
        "TMI_EMAIL",
    }
)


def fingerprint_text(text: str) -> str:
    normalized = _SPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _IDENTIFIER_SEPARATORS.sub("", normalized)


def grouping_key(
    *, record_id: str, group_id: str | None = None, template_id: str | None = None
) -> str:
    """The key that must stay on one side of a train/validation cut.

    ``validate_corpus`` rejects a ``group_id`` **or** a ``template_id`` that straddles two
    splits, so both have to drive the bucket. Keying on ``group_id or id`` alone let two rows
    sharing a template land on opposite sides and produce a corpus the project's own
    validator refuses — a failure that would surface only at the next load.
    """
    return group_id or template_id or record_id


def hashed_validation_split(key: str, percent: int) -> str:
    """Deterministic train/validation assignment shared by every human-label path."""
    if not 0 <= percent < 100:
        raise ValueError("validation percent must be in [0, 100)")
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "validation" if bucket < percent else "train"


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def connected_group_split(
    records: Sequence[_T],
    *,
    values: Callable[[_T], Iterable[str]],
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
    seed: str = "garim-v1",
) -> dict[str, list[_T]]:
    if not records:
        return {"train": [], "validation": [], "test": []}
    if len(ratios) != 3 or any(value < 0 for value in ratios) or sum(ratios) <= 0:
        raise ValueError("ratios must contain three non-negative values")

    union = _UnionFind(len(records))
    owner: dict[str, int] = {}
    for index, record in enumerate(records):
        for value in set(values(record)):
            if not value:
                continue
            previous = owner.setdefault(value, index)
            union.union(index, previous)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        components[union.find(index)].append(index)
    ordered = sorted(
        components.values(),
        key=lambda component: (
            -len(component),
            hashlib.sha256(f"{seed}:{component[0]}".encode()).hexdigest(),
        ),
    )

    names = ("train", "validation", "test")
    ratio_total = sum(ratios)
    targets = {
        name: len(records) * ratio / ratio_total
        for name, ratio in zip(names, ratios, strict=True)
    }
    assigned: dict[str, list[int]] = {name: [] for name in names}
    for component in ordered:
        destination = min(
            names,
            key=lambda name: (
                len(assigned[name]) / targets[name] if targets[name] else float("inf"),
                names.index(name),
            ),
        )
        assigned[destination].extend(component)
    return {
        name: [records[index] for index in sorted(indexes)]
        for name, indexes in assigned.items()
    }


def _kdpii_values(dialogue: dict[str, Any]) -> Iterable[str]:
    for sentence in dialogue.get("sentence", []):
        for entity in sentence.get("PNE", []):
            if entity.get("label") not in KDPII_IDENTIFIER_LABELS:
                continue
            form = entity.get("form")
            if isinstance(form, str):
                normalized = normalize_identifier(form)
                if normalized:
                    yield normalized


def split_kdpii_dialogues(
    dialogues: Sequence[dict[str, Any]],
    *,
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
    seed: str = "garim-kdpii-v1",
) -> dict[str, list[dict[str, Any]]]:
    """Split before preprocessing; repeated identifiers keep whole dialogues together."""
    result = connected_group_split(dialogues, values=_kdpii_values, ratios=ratios, seed=seed)
    assert_identifier_disjoint(result)
    return result


def assert_identifier_disjoint(splits: dict[str, Sequence[dict[str, Any]]]) -> None:
    owners: dict[str, str] = {}
    for split_name, dialogues in splits.items():
        for dialogue in dialogues:
            for value in set(_kdpii_values(dialogue)):
                previous = owners.setdefault(value, split_name)
                if previous != split_name:
                    raise ValueError(
                        f"normalized identifier appears in both {previous} and {split_name}"
                    )


def assert_fingerprint_disjoint(
    train_texts: Iterable[str], eval_texts: Iterable[str]
) -> None:
    train = {fingerprint_text(text) for text in train_texts}
    evaluation = {fingerprint_text(text) for text in eval_texts}
    overlap = train & evaluation
    if overlap:
        raise ValueError(f"train/eval fingerprint overlap: {len(overlap)}")
