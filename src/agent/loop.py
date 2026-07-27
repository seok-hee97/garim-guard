"""핸드롤 ReAct 루프 — 4 seam 오케스트레이션.

contents 를 유지하며 { generate → (before_tool 게이트) → 도구 실행 →
function_response 되먹임 } 를 반복한다. 병렬 호출(여러 tool_calls)을 전부 순회하고,
text-only 응답이면 종료한다. iteration cap 으로 무한 루프를 막는다.

4 seam:
  before_model  → 메시지 비식별/스팟라이트/레일 (D1 은 no-op)
  after_model   → per_hop 모더레이션 (D1 은 no-op)
  before_tool   → ★결정적 게이트★ (D1 은 no-op, D2 에서 Tier A)
  after_tool    → 출력 taint/DLP 래핑 (D1 은 no-op)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from ..providers.base import GenConfig, Msg, Provider, ToolResult
from . import tools as toolmod
from .hooks import Block, GuardrailHooks, NoOpHooks, Redacted, Rehydrated, Tainted_
from .taint import sources_of, unwrap
from .trace import EpisodeTrace, HopTrace


async def run_episode(
    user_input: str,
    provider: Provider,
    cfg: GenConfig,
    *,
    hooks: GuardrailHooks | None = None,
    world: Any = None,
    defenses: dict[str, bool] | None = None,
    max_iterations: int = 6,
    scenario: str = "",
    episode_id: str | None = None,
) -> EpisodeTrace:
    hooks = hooks or NoOpHooks()
    defenses = defenses or {}
    taint_on = defenses.get("tier_b", False)
    ep = EpisodeTrace(
        episode_id=episode_id or uuid.uuid4().hex[:12],
        user_input=user_input, scenario=scenario, defenses=dict(defenses),
    )
    t_ep = time.perf_counter()
    messages: list[Msg] = [Msg(role="user", text=user_input)]

    for _ in range(max_iterations):
        # ── before_model ──
        bm = await hooks.before_model(messages, ctx=ep)
        if isinstance(bm, Block):
            ep.finish_reason = "blocked"
            _record_block(ep, bm)
            break
        if isinstance(bm, Redacted):
            messages = bm.messages

        # ── 모델 호출 ──
        t0 = time.perf_counter()
        result = await provider.generate(messages, cfg)
        latency = int((time.perf_counter() - t0) * 1000)

        # ── after_model (per_hop) ──
        result = await hooks.after_model(result, ctx=ep)

        if result.blocked:
            ep.finish_reason = "blocked"
            ep.add(HopTrace(episode_id=ep.episode_id, hop=ep.total_hops,
                            gate_decision="BLOCK", gate_rule="safety_filter",
                            latency_ms=latency, note="모델 안전 필터 차단"))
            break

        # 모델 응답을 이력에 추가.
        # raw 를 실어 provider 가 원본 Content(thought_signature 포함)를 재전송하게 한다.
        messages.append(Msg(role="model", text=result.text,
                            tool_calls=result.tool_calls, raw=result.raw))

        # text-only → 종료
        if not result.tool_calls:
            ep.final_text = result.text
            ep.finish_reason = "done"
            break

        # ── 병렬 호출 전부 순회 ──
        tool_results: list[ToolResult] = []
        for call in result.tool_calls:
            hop = HopTrace(
                episode_id=ep.episode_id, hop=ep.total_hops,
                tool_name=call.name, tool_args=dict(call.args), call_id=call.call_id,
                attempted=True, latency_ms=latency,
            )

            # ── before_tool (★게이트★) ──
            args_for_exec = dict(call.args)
            gate = await hooks.before_tool(call, ctx=ep)
            if isinstance(gate, Block):
                hop.executed = False
                hop.gate_decision = gate.decision  # BLOCK | HITL_DENY
                hop.gate_rule = gate.rule
                hop.note = gate.reason
                hop.taint_sources = sorted(sources_of(call.args))
                ep.add(hop)
                tool_results.append(ToolResult(
                    call_id=call.call_id, name=call.name,
                    content={"blocked": True, "rule": gate.rule}, is_error=True))
                continue
            if isinstance(gate, Rehydrated):
                args_for_exec = gate.args
            hop.gate_decision = "ALLOW"

            # ── 도구 실행 ──
            try:
                raw = toolmod.execute(call.name, unwrap(args_for_exec), call.call_id,
                                      world=world, taint_enabled=taint_on)
                hop.executed = True
                hop.taint_sources = sorted(sources_of(raw))
            except Exception as e:  # noqa: BLE001 — 도구 오류도 트레이스에 남긴다
                hop.executed = False
                hop.note = f"tool_error: {type(e).__name__}: {e}"
                ep.add(hop)
                tool_results.append(ToolResult(
                    call_id=call.call_id, name=call.name,
                    content={"error": str(e)}, is_error=True))
                continue

            # ── after_tool ──
            # 실행된 홉은 after_tool(관측용 DLP/레일)의 예외와 무관하게 먼저 트레이스에 남긴다.
            # 그러지 않으면 '실행됐으나 기록 없는 홉'이 생겨 트레이스 기반 채점이 조용히 깨진다.
            ep.add(hop)
            try:
                wrapped: Tainted_ = await hooks.after_tool(
                    ToolResult(call_id=call.call_id, name=call.name, content=raw), ctx=ep)
                hop.rail_scores = wrapped.rail_scores
                output = unwrap(wrapped.output)
            except Exception as e:  # noqa: BLE001 — 관측용 훅 실패가 도구 기록·루프를 죽이지 않게
                hop.note = (hop.note + " | " if hop.note else "") + \
                    f"after_tool_error: {type(e).__name__}: {e}"
                output = raw
            tool_results.append(ToolResult(
                call_id=call.call_id, name=call.name, content=output))

        # 도구 결과를 다음 턴으로 되먹임 (정본: role="tool")
        messages.append(Msg(role="tool", tool_results=tool_results))
    else:
        ep.finish_reason = "iteration_cap"

    ep.total_latency_ms = int((time.perf_counter() - t_ep) * 1000)
    return ep


def _record_block(ep: EpisodeTrace, bm: Block) -> None:
    ep.add(HopTrace(episode_id=ep.episode_id, hop=ep.total_hops,
                    gate_decision="BLOCK", gate_rule=bm.rule, note=bm.reason))
