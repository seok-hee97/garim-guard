from __future__ import annotations

import json
from pathlib import Path

from garim.training.export import PII_LABELS, write_l1_metadata


def test_export_metadata_matches_runtime_contract(tmp_path: Path) -> None:
    path = write_l1_metadata(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(PII_LABELS) == 29
    assert payload["pii_labels"] == PII_LABELS
    assert payload["attack_labels"] == ["NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK"]
    assert len(payload["normalization_delta_order"]) == 8
