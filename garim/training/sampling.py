"""Deterministic PII:attack task interleaving without caching a full DataLoader."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import TypeVar

_T = TypeVar("_T")


def interleave_task_batches(
    pii_batches: Iterable[_T],
    attack_batches: Callable[[], Iterable[_T]],
    *,
    pii_per_attack: int = 4,
) -> Iterator[_T]:
    """Yield all PII batches and restart the small attack loader as needed at a fixed ratio."""
    if pii_per_attack < 1:
        raise ValueError("pii_per_attack must be positive")
    attack_iterator = iter(attack_batches())

    def next_attack() -> _T:
        nonlocal attack_iterator
        try:
            return next(attack_iterator)
        except StopIteration:
            attack_iterator = iter(attack_batches())
            try:
                return next(attack_iterator)
            except StopIteration as exc:
                raise ValueError("attack DataLoader yielded no batches") from exc

    pii_count = 0
    for batch in pii_batches:
        yield batch
        pii_count += 1
        if pii_count % pii_per_attack == 0:
            yield next_attack()
    if pii_count and pii_count % pii_per_attack:
        yield next_attack()


def deterministic_subset(
    records: Sequence[_T], limit: int | None, *, key: Callable[[_T], str], salt: str
) -> list[_T]:
    """상위 `limit` 개를 **안정 해시 순위**로 고른다.

    ## 왜 `random.sample` 이 아닌가

    이 선택은 봉인에 들어가고 원격에서 재생성돼 해시로 대조된다. 그러려면 **입력 순서와
    무관하게** 같은 결과가 나와야 한다. `random.sample` 은 리스트 순서에 의존하므로
    parquet 파일을 하나 더 받거나 glob 순서가 달라지면 다른 집합이 나온다.

    레코드 id 를 해시해 순위를 매기면 순서가 어떻든 같은 부분집합이 나오고, 원천이
    커져도 이미 뽑힌 것 대부분이 그대로 남는다(안정성).

    `salt` 는 용도를 가른다 — 같은 원천에서 학습용과 다른 목적으로 두 번 뽑을 때 같은
    행이 겹치지 않게 한다.
    """
    if limit is None or limit >= len(records):
        return list(records)
    if limit < 0:
        raise ValueError("limit must not be negative")
    ranked = sorted(
        records,
        key=lambda record: hashlib.sha256(f"{salt}:{key(record)}".encode()).hexdigest(),
    )
    return ranked[:limit]
