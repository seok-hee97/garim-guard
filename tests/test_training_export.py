from __future__ import annotations

import json
from pathlib import Path

from garim.detect.l1 import NORMALIZATION_DELTA_ORDER
from garim.training.export import PII_LABELS, write_l1_metadata


def test_export_metadata_matches_runtime_contract(tmp_path: Path) -> None:
    path = write_l1_metadata(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(PII_LABELS) == 29
    assert payload["pii_labels"] == PII_LABELS
    assert payload["attack_labels"] == ["NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK"]
    # ★ 폭을 숫자로 박으면 계약이 갈렸을 때 이 테스트가 함께 틀린다. 실제로 갈렸다 —
    #   `space` 를 feature 에서 뺀 뒤에도 메타데이터는 8칸을 적고 있었고, 런타임
    #   어댑터는 이 파일을 읽어 입력을 만든다. 학습·내보내기·추론이 서로 다른 폭을
    #   믿는 상태였다. 단일 출처에서 끌어온다.
    assert payload["normalization_delta_order"] == list(NORMALIZATION_DELTA_ORDER)
    assert "space" not in payload["normalization_delta_order"]
