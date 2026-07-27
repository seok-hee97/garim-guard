"""프로덕션 런타임 하드닝 회귀 — fail-closed seam · 벽시계 예산 · 엄격 스키마.

'실제 구동 시스템' 전환에서 추가한 안전 성질을 고정한다:
  - before_model/before_tool 예외 = 크래시가 아니라 fail-closed(도구 미실행)
  - deadline 초과 = 우아한 종료
  - 엄격 스키마(프로덕션) = 미선언 인자 거부
"""

from __future__ import annotations

import copy

import pytest

from src.config import gen_config, load_policy
from src.agent.loop import run_episode
from src.agent.hooks import NoOpHooks
from src.agent.policy import TierAGate
from src.agent.toolworld import default_world
from src.providers.base import ToolCall
from src.providers.fake import FakeProvider


def _cfg():
    return gen_config(load_policy())


@pytest.mark.asyncio
async def test_before_model_exception_fails_closed():
    """before_model(보안 seam) 예외 → 크래시 아님, finish='error', 도구 미실행."""
    class BMBoom(NoOpHooks):
        async def before_model(self, messages, ctx=None):
            raise RuntimeError("before_model 크래시 시뮬")

    provider = FakeProvider.scripted([[("search_kb", {"query": "x"})], "done"])
    ep = await run_episode("x", provider, _cfg(), hooks=BMBoom(), world=default_world())
    assert ep.finish_reason == "error"
    assert not any(h.executed for h in ep.hops)             # 도구가 실행되지 않음
    assert any(h.gate_rule == "before_model.fail_closed" for h in ep.hops)


@pytest.mark.asyncio
async def test_after_model_exception_fails_closed():
    """after_model(출력 모더레이션 seam) 예외 → 크래시 아님, finish='error'(3 seam 대칭)."""
    class AMBoom(NoOpHooks):
        async def after_model(self, result, ctx=None):
            raise RuntimeError("after_model 크래시 시뮬")

    provider = FakeProvider.scripted([[("search_kb", {"query": "x"})], "done"])
    ep = await run_episode("x", provider, _cfg(), hooks=AMBoom(), world=default_world())
    assert ep.finish_reason == "error"
    assert not any(h.executed for h in ep.hops)
    assert any(h.gate_rule == "after_model.fail_closed" for h in ep.hops)


@pytest.mark.asyncio
async def test_before_tool_gate_exception_fails_closed():
    """before_tool(게이트) 예외 → fail-open 아님, 해당 도구 차단(gate.fail_closed)."""
    class GateBoom(NoOpHooks):
        async def before_tool(self, call, ctx=None):
            raise RuntimeError("게이트 크래시 시뮬")

    provider = FakeProvider.scripted([[("send_email", {"to": "x@y.z", "subject": "s", "body": "b"})],
                                      "done"])
    ep = await run_episode("x", provider, _cfg(), hooks=GateBoom(), world=default_world())
    hop = next(h for h in ep.hops if h.tool_name == "send_email")
    assert hop.executed is False                            # 게이트 실패 = 도구 미실행(fail-closed)
    assert hop.gate_rule == "gate.fail_closed"


@pytest.mark.asyncio
async def test_deadline_exceeded_terminates_gracefully():
    """벽시계 예산 초과 → deadline_exceeded 로 우아하게 종료(무한 점유 방지)."""
    provider = FakeProvider.scripted([[("search_kb", {"query": "x"})], "done"])
    ep = await run_episode("x", provider, _cfg(), hooks=NoOpHooks(),
                           world=default_world(), deadline_s=-1.0)   # 즉시 초과
    assert ep.finish_reason == "deadline_exceeded"
    assert not any(h.executed for h in ep.hops)


def test_strict_schema_rejects_undeclared_arg():
    """엄격 스키마(프로덕션) → 선언되지 않은 인자를 실도구에 넘기기 전에 거부."""
    policy = copy.deepcopy(load_policy())
    policy.setdefault("tier_a", {})["strict_schema"] = True
    gate = TierAGate(policy)
    b = gate.check(ToolCall("send_email",
        {"to": "support@company.example.com", "subject": "s", "body": "b",
         "cc": "attacker@evil.com"}, "send_email#0"))          # cc 는 미선언 인자
    assert b is not None and b.rule.startswith("arg_schema.undeclared")


def test_strict_schema_off_by_default_allows_extra_arg():
    """기본(엄격 off) → 평가 재현성 보존을 위해 미선언 인자를 막지 않는다."""
    gate = TierAGate(load_policy())                            # strict_schema 없음 → off
    b = gate.check(ToolCall("send_email",
        {"to": "support@company.example.com", "subject": "s", "body": "b",
         "cc": "x@y.z"}, "send_email#0"))
    # egress 는 통과(allowlist 내 to), 미선언 cc 도 기본 모드에선 스키마 차단 안 함
    assert b is None or b.rule != "arg_schema.undeclared:cc"
