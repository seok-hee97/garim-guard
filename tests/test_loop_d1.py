"""D1 체크포인트 테스트 — API 없이 FakeProvider 로 루프·트레이스를 검증한다.

D1 산출 기준: 정상 한국어 과제 end-to-end 성공 + 트레이스 JSON 기록.
"""

from __future__ import annotations

import pytest

from src.config import gen_config, load_policy
from src.providers.base import GenConfig, GenResult, Msg, ToolCall
from src.providers.fake import FakeProvider
from src.agent.loop import run_episode


def _cfg() -> GenConfig:
    return gen_config(load_policy())


@pytest.mark.asyncio
async def test_normal_task_end_to_end():
    """조회 → 이메일 발송 → 텍스트 종료. 무방어 arm 이 정상 완주한다."""
    provider = FakeProvider.scripted([
        [("lookup_customer", {"phone": "010-1234-5678"})],
        [("send_email", {"to": "support@company.example.com", "subject": "안내", "body": "처리됨"})],
        "고객님 문의를 처리했습니다.",
    ])
    ep = await run_episode("환불 상태를 이메일로 알려줘", provider, _cfg())

    assert ep.finish_reason == "done"
    assert ep.final_text == "고객님 문의를 처리했습니다."
    assert ep.any_executed("lookup_customer")
    assert ep.any_executed("send_email", to="support@company.example.com")
    # 무방어(NoOp) arm 이므로 전부 실행됨
    assert all(h.executed for h in ep.hops if h.tool_name)


@pytest.mark.asyncio
async def test_parallel_tool_calls_all_traversed():
    """★ 한 턴에 병렬 호출 2개 → 둘 다 트레이스에 남아야 한다 (parts[0]만 보면 실패). ★"""
    provider = FakeProvider(script=[
        GenResult(text="", finish_reason="tool_calls", tool_calls=[
            ToolCall("lookup_customer", {"phone": "010-1111-2222"}, "lookup_customer#0"),
            ToolCall("search_kb", {"query": "환불 규정"}, "search_kb#1"),
        ]),
        GenResult(text="완료", finish_reason="stop"),
    ])
    ep = await run_episode("고객 조회하고 규정 찾아줘", provider, _cfg())

    tool_hops = [h for h in ep.hops if h.tool_name]
    assert len(tool_hops) == 2, "병렬 호출 2개가 모두 순회되어야 한다"
    assert {h.tool_name for h in tool_hops} == {"lookup_customer", "search_kb"}


@pytest.mark.asyncio
async def test_trace_has_attempted_executed_from_hop0():
    """attempted/executed 필드가 hop 0 부터 기록된다 (ASR 분해의 전제)."""
    provider = FakeProvider.scripted([[("search_kb", {"query": "x"})], "끝"])
    ep = await run_episode("검색", provider, _cfg())
    h0 = next(h for h in ep.hops if h.tool_name)
    assert h0.attempted is True
    assert h0.executed is True
    assert h0.gate_decision == "ALLOW"


@pytest.mark.asyncio
async def test_iteration_cap():
    """도구만 무한 반복하는 모델도 cap 에서 멈춘다."""
    provider = FakeProvider(script=[
        GenResult(text="", finish_reason="tool_calls",
                  tool_calls=[ToolCall("search_kb", {"query": "loop"}, "search_kb#0")])
        for _ in range(20)
    ])
    ep = await run_episode("무한", provider, _cfg(), max_iterations=4)
    assert ep.finish_reason == "iteration_cap"
    assert len([h for h in ep.hops if h.tool_name]) == 4


@pytest.mark.asyncio
async def test_afc_must_be_disabled():
    """automatic_function_calling=True 는 계약 위반 → ValueError."""
    provider = FakeProvider.scripted(["x"])
    cfg = _cfg()
    cfg.automatic_function_calling = True
    with pytest.raises(ValueError):
        await run_episode("x", provider, cfg)


# 봉인 테스트는 tests/test_rail_cannot_override_gate.py 로 이동(policy.yaml 이 참조하는 파일).
