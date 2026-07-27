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

import asyncio
import time
import uuid
from typing import Any

from ..providers.base import GenConfig, Msg, Provider, ToolResult
from . import log as alog
from . import tools as toolmod
from .hooks import Block, GuardrailHooks, NoOpHooks, Redacted, Rehydrated, Tainted_
from .taint import sources_of, unwrap
from .trace import EpisodeTrace, HopTrace

_log = alog.get_logger()


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
    deadline_s: float = 60.0,
    per_call_timeout_s: float = 30.0,
) -> EpisodeTrace:
    # ★ 설정 불변식은 진입에서 fail-fast (오설정=프로그래밍 오류, 런타임 예외 처리와 구분).
    #   런타임/일시 예외(429·타임아웃)는 아래 루프에서 잡아 우아하게 error 종료한다.
    if cfg.automatic_function_calling:
        raise ValueError("automatic_function_calling 은 반드시 False (before_tool 게이트 보호)")
    hooks = hooks or NoOpHooks()
    defenses = defenses or {}
    taint_on = defenses.get("tier_b", False)
    ep = EpisodeTrace(
        episode_id=episode_id or uuid.uuid4().hex[:12],
        user_input=user_input, scenario=scenario, defenses=dict(defenses),
    )
    alog.bind(episode_id=ep.episode_id)
    t_ep = time.perf_counter()
    messages: list[Msg] = [Msg(role="user", text=user_input)]

    for _ in range(max_iterations):
        # ── 벽시계 예산 (느린 도구/모델이 요청을 무한정 붙잡지 못하게) ──
        if time.perf_counter() - t_ep > deadline_s:
            ep.finish_reason = "deadline_exceeded"
            alog.event(_log, "warning", "deadline_exceeded", hops=ep.total_hops)
            break

        # ── before_model (★fail-closed★ — 보안 seam 실패는 크래시가 아니라 안전한 종료) ──
        try:
            bm = await hooks.before_model(messages, ctx=ep)
        except Exception as e:  # noqa: BLE001 — DLP redact 등 실패 시 원본을 모델에 넘기지 않고 종료
            ep.finish_reason = "error"
            _record_block(ep, Block(rule="before_model.fail_closed",
                                    reason=f"{type(e).__name__}: {e}"))
            alog.event(_log, "error", "before_model.fail_closed", err=type(e).__name__)
            break
        if isinstance(bm, Block):
            ep.finish_reason = "blocked"
            _record_block(ep, bm)
            break
        if isinstance(bm, Redacted):
            messages = bm.messages

        # ── 모델 호출 (provider 재시도 소진/영구실패 시 우아하게 error 종료) ──
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(provider.generate(messages, cfg),
                                            timeout=per_call_timeout_s)
        except (Exception, asyncio.TimeoutError) as e:  # noqa: BLE001
            ep.finish_reason = "error"
            ep.add(HopTrace(episode_id=ep.episode_id, hop=ep.total_hops, gate_decision="NA",
                            note=f"provider_error: {type(e).__name__}: {e}"))
            alog.event(_log, "error", "provider_error", err=type(e).__name__)
            break
        latency = int((time.perf_counter() - t0) * 1000)

        # ── after_model (per_hop 모더레이션) · ★fail-closed★ ──
        #    출력 모더레이션 seam 실패도 크래시가 아니라 안전한 종료로(3 seam 대칭).
        try:
            result = await hooks.after_model(result, ctx=ep)
        except Exception as e:  # noqa: BLE001
            ep.finish_reason = "error"
            _record_block(ep, Block(rule="after_model.fail_closed",
                                    reason=f"{type(e).__name__}: {e}"))
            alog.event(_log, "error", "after_model.fail_closed", err=type(e).__name__)
            break

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

        # text-only → 종료. ★신뢰 경계(에이전트→사용자)에서만 vault.restore★
        #   외부 싱크로는 토큰을 유지하되, 최종 사용자 응답에는 원본을 복원한다.
        if not result.tool_calls:
            vault = getattr(hooks, "vault", None)
            ep.final_text = vault.restore(result.text) if vault is not None else result.text
            ep.finish_reason = "done"
            alog.event(_log, "info", "episode_done", hops=ep.total_hops)
            break

        # ── 병렬 호출 전부 순회 ──
        tool_results: list[ToolResult] = []
        for call in result.tool_calls:
            hop = HopTrace(
                episode_id=ep.episode_id, hop=ep.total_hops,
                tool_name=call.name, tool_args=dict(call.args), call_id=call.call_id,
                attempted=True, latency_ms=latency,
            )

            # ── before_tool (★게이트 · fail-closed★) ──
            #    게이트 자체가 예외를 던지면 fail-open(통과)이 아니라 차단으로 변환한다.
            args_for_exec = dict(call.args)
            try:
                gate = await hooks.before_tool(call, ctx=ep)
            except Exception as e:  # noqa: BLE001 — 게이트 실패 = fail-closed 차단
                gate = Block(rule="gate.fail_closed", reason=f"{type(e).__name__}: {e}")
                alog.event(_log, "error", "gate.fail_closed", tool=call.name, err=type(e).__name__)
            if isinstance(gate, Block):
                hop.executed = False
                hop.gate_decision = gate.decision  # BLOCK | HITL_DENY
                hop.gate_rule = gate.rule
                hop.note = gate.reason
                hop.taint_sources = sorted(sources_of(call.args))
                ep.add(hop)
                alog.event(_log, "info", "gate_block", tool=call.name,
                           decision=gate.decision, rule=gate.rule)
                tool_results.append(ToolResult(
                    call_id=call.call_id, name=call.name,
                    content={"blocked": True, "rule": gate.rule}, is_error=True))
                continue
            if isinstance(gate, Rehydrated):
                args_for_exec = gate.args
            hop.gate_decision = "ALLOW"

            # ── 도구 실행 (실도구가 걸려도 요청을 붙잡지 못하게 스레드+타임아웃) ──
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(toolmod.execute, call.name, unwrap(args_for_exec),
                                      call.call_id, world=world, taint_enabled=taint_on),
                    timeout=per_call_timeout_s)
                hop.executed = True
                hop.taint_sources = sorted(sources_of(raw))
            except (Exception, asyncio.TimeoutError) as e:  # noqa: BLE001 — 도구 오류도 트레이스에
                hop.executed = False
                hop.note = f"tool_error: {type(e).__name__}: {e}"
                ep.add(hop)
                alog.event(_log, "error", "tool_error", tool=call.name, err=type(e).__name__)
                tool_results.append(ToolResult(
                    call_id=call.call_id, name=call.name,
                    content={"error": str(e)}, is_error=True))
                continue

            # ── after_tool ──
            # 실행된 홉은 after_tool 예외와 무관하게 먼저 트레이스에 남긴다(기록 소실 방지).
            # 관측용 레일 채점 실패는 after_tool 내부에서 삼켜지므로 여기까지 오지 않는다.
            # 여기로 전파되는 예외 = DLP 비식별 실패 → ★fail-CLOSED★: 원본(raw)을 절대 모델에
            # 넘기지 않는다(config policy.yaml `dlp: fail: closed` 준수). 원본 PII 누출 차단.
            ep.add(hop)
            is_error = False
            try:
                wrapped: Tainted_ = await hooks.after_tool(
                    ToolResult(call_id=call.call_id, name=call.name, content=raw), ctx=ep)
                hop.rail_scores = wrapped.rail_scores
                output: Any = unwrap(wrapped.output)
            except Exception as e:  # noqa: BLE001 — DLP 비식별 실패는 fail-closed(원본 통과 금지)
                hop.note = (hop.note + " | " if hop.note else "") + \
                    f"dlp_fail_closed: {type(e).__name__}: {e}"
                hop.gate_decision = "BLOCK"
                hop.gate_rule = "dlp.fail_closed"
                output = {"blocked": True, "rule": "dlp.fail_closed",
                          "error": "출력 비식별 실패로 차단됨(원본 미전달)"}
                is_error = True
            tool_results.append(ToolResult(
                call_id=call.call_id, name=call.name, content=output, is_error=is_error))

        # 도구 결과를 다음 턴으로 되먹임 (정본: role="tool")
        messages.append(Msg(role="tool", tool_results=tool_results))
    else:
        ep.finish_reason = "iteration_cap"

    ep.total_latency_ms = int((time.perf_counter() - t_ep) * 1000)
    return ep


def _record_block(ep: EpisodeTrace, bm: Block) -> None:
    ep.add(HopTrace(episode_id=ep.episode_id, hop=ep.total_hops,
                    gate_decision="BLOCK", gate_rule=bm.rule, note=bm.reason))
