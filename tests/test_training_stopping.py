from __future__ import annotations

from garim.training.stopping import MultiTaskEarlyStopping


def test_early_stopping_waits_until_both_tasks_are_stale() -> None:
    stopping = MultiTaskEarlyStopping(patience=2)
    assert not stopping.update(pii_recall=0.8, attack_macro_recall=0.7)
    assert not stopping.update(pii_recall=0.8, attack_macro_recall=0.71)
    assert not stopping.update(pii_recall=0.8, attack_macro_recall=0.72)
    assert not stopping.update(pii_recall=0.8, attack_macro_recall=0.72)
    assert stopping.update(pii_recall=0.8, attack_macro_recall=0.72)
