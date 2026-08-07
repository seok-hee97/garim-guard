"""Deterministic PII:attack task interleaving without caching a full DataLoader."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
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
