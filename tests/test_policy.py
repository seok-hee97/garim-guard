from pathlib import Path

import pytest
from pydantic import ValidationError

from garim import Inspector
from garim.policy import PolicyConfig


def test_code_override_can_lower_rrn_anchor_to_mask() -> None:
    config = PolicyConfig(codes={"L-KR-01": "mask"})
    result = Inspector(config).check("주민번호: 200101-3000000")
    assert result.verdict == "mask"
    assert result.masked_text == "주민번호: <KR_RRN_1>"


def test_invalid_policy_fails_closed(tmp_path: Path) -> None:
    policy = tmp_path / "bad.yaml"
    policy.write_text("version: 1\nunknown: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Inspector(policy)
