from __future__ import annotations

import pytest

from garim import Inspector
from garim.detect.l1 import L1Entity, L1Output
from garim.policy.schema import Limits, PolicyConfig


class FakeBackend:
    name = "fake"

    def infer(self, text: str, **_: object) -> L1Output:
        return L1Output(
            entities=[L1Entity("PERSON", (0, 3), 0.9)],
            attack_probabilities={
                "NONE": 0.05,
                "INJECTION": 0.9,
                "JAILBREAK": 0.03,
                "SYSTEM_LEAK": 0.02,
            },
            romanized_evasion=0.7,
        )


class BrokenBackend:
    name = "broken"

    def infer(self, text: str, **_: object) -> L1Output:
        raise RuntimeError("inference failed")


def test_l1_four_head_contract_merges_with_l0() -> None:
    result = Inspector(l1_backend=FakeBackend()).check("홍길동 요청")
    assert any(finding.code == "L-CT-01" for finding in result.leaks)
    attack = next(finding for finding in result.attacks if finding.layer == "L1")
    assert attack.code == "A-INJ-01"
    assert "A-KO-03" in attack.evasion
    assert not result.degraded


def test_l1_failure_keeps_l0_result_and_sets_degraded() -> None:
    result = Inspector(l1_backend=BrokenBackend()).check("user@example.test")
    assert result.verdict == "mask"
    assert result.masked_text == "<EMAIL_1>"
    assert result.degraded


def test_l1_entity_boundary_does_not_absorb_trailing_whitespace() -> None:
    class TrailingSpaceBackend:
        name = "trailing-space"

        def infer(self, text: str, **_: object) -> L1Output:
            return L1Output(entities=[L1Entity("PERSON", (0, 4), 0.99)])

    result = Inspector(l1_backend=TrailingSpaceBackend()).check("홍길동 요청")
    person = next(finding for finding in result.leaks if finding.code == "L-CT-01")
    assert person.span == (0, 3)
    assert result.masked_text == "<PERSON_1> 요청"


def test_policy_chunk_size_cannot_exceed_backend_limit() -> None:
    class BoundedBackend:
        name = "bounded"
        max_length = 512

        def infer(self, text: str, **_: object) -> L1Output:
            return L1Output()

    config = PolicyConfig(limits=Limits(chunk_size=1024, chunk_overlap=64))
    with pytest.raises(ValueError, match="cannot exceed"):
        Inspector(config, l1_backend=BoundedBackend())


def test_onnx_backend_cancels_only_the_timed_out_worker() -> None:
    """프록시는 백엔드 하나를 요청 여럿이 공유한다.

    한 요청의 타임아웃이 남의 추론을 끊으면 안 된다.
    """
    import threading
    from types import SimpleNamespace

    from garim.detect.l1 import OnnxL1Backend

    backend = object.__new__(OnnxL1Backend)
    backend._run_options_lock = threading.Lock()
    mine, other = SimpleNamespace(terminate=False), SimpleNamespace(terminate=False)
    backend._active_run_options = {1: mine, 2: other}
    backend.cancel_current(1)
    assert mine.terminate
    assert not other.terminate


def test_cli_refuses_to_run_without_the_requested_l1_model(tmp_path, capsys) -> None:  # noqa: ANN001
    """모델을 달라고 했는데 못 올렸으면 L0 만으로 조용히 돌지 않는다."""
    from garim.cli import main

    assert main(["--l1-model-dir", str(tmp_path), "check", "안녕하세요"]) == 4
    assert "garim:" in capsys.readouterr().err
