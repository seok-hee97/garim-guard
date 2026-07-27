"""봉인 테스트 — 분류기는 결정적 게이트를 무효화할 수 없다.

두 가지를 단언한다:
  (a) 스키마 강제: classifiers.*.mode != log_only → ValueError.
      → 'benign' 판정이 게이트를 무효화하는 경로가 애초에 만들어질 수 없다.
  (b) 합성 봉인(fail-open): classifier_gate 와 tier_a 가 동시에 켜져도, 분류기의
      benign(비격리) 판정이 Tier A 의 Block 을 override 할 수 없다. classifier_gate 는
      오직 '추가 차단'만 가능하다.

이것이 "온디바이스 분류기를 추가해도 게이트는 여전히 견고한 바닥"이라는 구조적 보증이다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import _validate_rails_log_only, load_policy
from src.agent.policy import DefenseHooks
from src.agent.toolworld import ATTACKER_EMAIL
from src.providers.base import ToolCall


# ── (a) 스키마 강제 ──

def test_classifier_mode_must_be_log_only():
    bad = {"classifiers": {"kanana_injection": {"mode": "block"}}}
    with pytest.raises(ValueError, match="log_only"):
        _validate_rails_log_only(bad)


def test_classifier_underscore_keys_skipped():
    """_contract 같은 메타 키는 검사에서 제외된다."""
    ok = {"classifiers": {"_contract": "설명", "r": {"mode": "log_only"}}}
    _validate_rails_log_only(ok)  # 예외 없음


def test_real_policy_passes_log_only_validation():
    _validate_rails_log_only(load_policy())  # 실제 policy.yaml 이 규약 준수


# ── (b) 합성 봉인: classifier benign 이 Tier A Block 을 override 못 함 ──

class _StubRail:
    """항상 benign(0.0) 을 반환하는 분류기 — '분류기가 통과시켜도' 를 시뮬레이션."""
    name = "stub_benign"
    def score(self, text: str) -> float:
        return 0.0


def _ctx(quarantined: bool = False):
    return SimpleNamespace(untrusted_corpus=[], quarantined=quarantined)


@pytest.mark.asyncio
async def test_benign_classifier_cannot_override_tier_a_block():
    """분류기가 benign 이라도 Tier A 의 egress Block 은 그대로 반환된다."""
    policy = load_policy()
    hooks = DefenseHooks(policy,
                         defenses={"tier_a": True, "classifier_gate": {"enable": True}},
                         rail=_StubRail())
    call = ToolCall("send_email",
                    {"to": ATTACKER_EMAIL, "subject": "s", "body": "b"}, "send_email#0")
    # 분류기가 격리하지 않은(benign) 상태
    decision = await hooks.before_tool(call, ctx=_ctx(quarantined=False))
    from src.agent.hooks import Block
    assert isinstance(decision, Block)
    assert decision.rule == "egress.email_domains"   # ← 분류기가 아니라 Tier A 가 막았다


@pytest.mark.asyncio
async def test_classifier_gate_can_only_add_blocks():
    """classifier_gate 는 Tier A 가 허용한 것 위에 '추가 차단'만 한다 (격리 시 고위험 도구)."""
    policy = load_policy()
    hooks = DefenseHooks(policy,
                         defenses={"tier_a": True, "classifier_gate": {"enable": True}},
                         rail=_StubRail())
    # 허용된 수신자 → Tier A 는 통과(단 send_email 은 hitl 없음/external). 격리 상태면 분류기가 차단.
    call = ToolCall("send_email",
                    {"to": "support@company.example.com", "subject": "s", "body": "b"},
                    "send_email#0")
    from src.agent.hooks import Block
    # 비격리: Tier A 통과 → None
    assert await hooks.before_tool(call, ctx=_ctx(quarantined=False)) is None
    # 격리: 분류기가 고위험 도구 추가 차단
    blocked = await hooks.before_tool(call, ctx=_ctx(quarantined=True))
    assert isinstance(blocked, Block) and blocked.rule == "classifier_gate.quarantined"


@pytest.mark.asyncio
async def test_classifier_gate_fail_open_without_tier_a():
    """classifier_gate 단독(Tier A off): 격리 아니면 통과(fail-open), 격리면 고위험 차단.

    이 arm 이 negative control — 난독화로 분류기가 격리 못 하면 공격이 통과한다.
    """
    policy = load_policy()
    hooks = DefenseHooks(policy,
                         defenses={"classifier_gate": {"enable": True}},  # tier_a off
                         rail=_StubRail())
    call = ToolCall("send_email",
                    {"to": ATTACKER_EMAIL, "subject": "s", "body": "b"}, "send_email#0")
    # 비격리(분류기 우회됨) → 공격자 이메일도 통과 = 붕괴 (Tier A 가 없으므로)
    assert await hooks.before_tool(call, ctx=_ctx(quarantined=False)) is None
