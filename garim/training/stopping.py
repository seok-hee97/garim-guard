"""Multi-objective stopping state without ML framework dependencies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MultiTaskEarlyStopping:
    patience: int = 2
    min_delta: float = 1e-4
    best_pii_recall: float = float("-inf")
    best_attack_recall: float = float("-inf")
    pii_stale: int = 0
    attack_stale: int = 0

    def update(self, *, pii_recall: float, attack_macro_recall: float) -> bool:
        if pii_recall > self.best_pii_recall + self.min_delta:
            self.best_pii_recall = pii_recall
            self.pii_stale = 0
        else:
            self.pii_stale += 1
        if attack_macro_recall > self.best_attack_recall + self.min_delta:
            self.best_attack_recall = attack_macro_recall
            self.attack_stale = 0
        else:
            self.attack_stale += 1
        return self.pii_stale >= self.patience and self.attack_stale >= self.patience
