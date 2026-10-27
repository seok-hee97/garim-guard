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


def hashed_validation_split(key: str, percent: int) -> str:
    """Deterministic train/validation assignment shared by every human-label path."""
    if not 0 <= percent < 100:
        raise ValueError("validation percent must be in [0, 100)")
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "validation" if bucket < percent else "train"


def component_validation_splits(records: Iterable[Any], percent: int) -> dict[str, str]:
    """Cut train/validation per connected component of shared group/template/pair keys.

    ★ 회귀 — 초판은 행마다 `group_id or template_id or id` 하나로 버킷을 정했다.
      `validate_corpus` 는 group **와** template 이 각각 한쪽에만 있기를 요구하는데,
      합성 공격은 template 300개 전부가 group 여럿에 걸쳐 있어서(group 은 template
      안에 중첩된다) 같은 template 이 양쪽으로 갈렸다. RunPod 리허설에서
      `template 'benign-076' crosses dataset splits` 로 7단계가 멈췄다 — GPU 교사
      실행 **뒤에** 처음 드러날 자리였다.

    키를 공유하는 행을 union-find 로 묶고, 성분마다 **가장 굵은 키**(template → group
    → id) 중 가장 작은 것으로 버킷을 정한다. group 이 template 안에 중첩되는 한, 어떤
    부분집합(교사 합의분·사람 검수분)에서 계산해도 한 template 은 같은 쪽에 떨어진다.
    중첩이 깨진 데이터는 `validate_corpus` 가 봉인 시점에 거부한다.
    """
    materialized = list(records)
    finder = _UnionFind(len(materialized))
    owners: dict[tuple[str, str], int] = {}
    for index, record in enumerate(materialized):
        for kind in ("group_id", "template_id", "pair_id"):
            value = getattr(record, kind, None)
            if value is None:
                continue
            owner = owners.setdefault((kind, value), index)
            if owner != index:
                finder.union(owner, index)
    components: dict[int, list[Any]] = defaultdict(list)
    for index, record in enumerate(materialized):
        components[finder.find(index)].append(record)
    splits: dict[str, str] = {}
    for members in components.values():
        # ★ R2 생성기는 family 단위로 검증을 고정한다(`meta.split_pinned`). 해시로 다시 나누면
        #   공격 family 9개가 전부 train 에 떨어져 seed 선택용 검증에 SYSTEM_LEAK 이 한 행뿐이었다.
        pinned = {
            member.split
            for member in members
            if (getattr(member, "meta", None) or {}).get("split_pinned")
        }
        if len(pinned) > 1:
            raise ValueError(f"pinned splits disagree inside one component: {sorted(pinned)}")
        if pinned:
            split = pinned.pop()
        else:
            templates = sorted(member.template_id for member in members if member.template_id)
            groups = sorted(member.group_id for member in members if member.group_id)
            anchor = (templates or groups or sorted(member.id for member in members))[0]
            split = hashed_validation_split(anchor, percent)
        for member in members:
            splits[member.id] = split
    return splits


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
