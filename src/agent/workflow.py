"""에이전트 워크플로 관측 계층.

보안 런타임의 실행 의미를 바꾸지 않고 Provider 를 감싸서 매 턴의 실제 입력/출력을
기록한다. CLI 상세 모드와 브라우저 데모 UI가 같은 정본 데이터를 사용한다.

중요:
- scripted provider 는 모델 추론이 아니라 고정 재생임을 메타데이터에 명시한다.
- 실 provider 의 raw 응답(thought_signature 포함)은 절대 직렬화하지 않는다.
- 도구 실행 전 원본 결과는 EpisodeTrace 메모리에만 존재한다. 호출자가 명시적으로
  include_raw=True 를 준 로컬 합성 데모에서만 응답 문서에 포함한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from ..providers.base import GenConfig, GenResult, Msg, Provider
from .taint import unwrap
from .trace import EpisodeTrace, HopTrace

_TOKEN_RE = re.compile(r"<([A-Z_]+)_\d+>")


def _jsonable(value: Any) -> Any:
    value = unwrap(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if is_dataclass(value):
        return _jsonable(asdict(value))
    return str(value)


def _message_view(message: Msg) -> dict[str, Any]:
    return {
        "role": message.role,
        "text": message.text,
        "tool_calls": [
            {"name": c.name, "args": _jsonable(c.args), "call_id": c.call_id}
            for c in message.tool_calls
        ],
        "tool_results": [
            {
                "name": r.name,
                "content": _jsonable(r.content),
                "call_id": r.call_id,
                "is_error": r.is_error,
            }
            for r in message.tool_results
        ],
    }


def _result_view(result: GenResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "tool_calls": [
            {"name": c.name, "args": _jsonable(c.args), "call_id": c.call_id}
            for c in result.tool_calls
        ],
        "blocked": result.blocked,
        "finish_reason": result.finish_reason,
        "usage": _jsonable(result.usage),
    }


@dataclass
class RecordedTurn:
    turn: int
    messages: list[dict[str, Any]]
    output: dict[str, Any] | None = None
    error: str = ""


class WorkflowRecorderProvider:
    """Provider passthrough + 매 턴 관측. 생성 결과에는 손대지 않는다."""

    def __init__(self, provider: Provider, *, mode: str, label: str = ""):
        self.provider = provider
        self.mode = mode
        self.name = getattr(provider, "name", type(provider).__name__)
        self.label = label or self.name
        self.turns: list[RecordedTurn] = []

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        turn = RecordedTurn(
            turn=len(self.turns) + 1,
            messages=[_message_view(m) for m in messages],
        )
        self.turns.append(turn)
        try:
            result = await self.provider.generate(messages, cfg)
        except Exception as exc:
            turn.error = f"{type(exc).__name__}: {exc}"
            raise
        turn.output = _result_view(result)
        return result


def _tokens(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            found.update(_TOKEN_RE.findall(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(value)
    return sorted(found)


def workflow_document(
    episode: EpisodeTrace,
    recorder: WorkflowRecorderProvider,
    *,
    rail_name: str,
    defenses: dict[str, Any],
    provider_label: str,
    model: str,
    include_raw: bool = False,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """EpisodeTrace 와 provider 턴을 브라우저/CLI 공통 문서로 결합한다."""

    tool_hops = [h for h in episode.hops if h.tool_name]
    # ★call_id 로 결합한다★ — 위치(index)로 짝지으면 호출 수와 hop 수가 어긋나는 순간
    # 엉뚱한 호출에 엉뚱한 판정이 붙는다. 어긋나는 경로는 실제로 있다:
    #   loop.py: calls = result.tool_calls[:MAX_PARALLEL_CALLS]   ← 8개 초과분 절단
    #   loop.py: 배치 순회 중 deadline 초과 시 break                ← 남은 호출은 hop 없음
    # 재현 결과(위치 결합 시): 절단된 lookup_customer 에 다음 턴 send_email 의
    # BLOCK/egress.email_domains 가 붙고, 정작 진짜 차단은 NA 로 사라졌다.
    # scripted 데모는 턴당 호출이 1개라 안 걸리지만 live provider 는 다중 호출을 낸다.
    by_id: dict[str, list[HopTrace]] = {}
    for h in tool_hops:
        if h.call_id:
            by_id.setdefault(h.call_id, []).append(h)
    unkeyed = [h for h in tool_hops if not h.call_id]   # call_id 를 안 주는 provider 폴백
    turns: list[dict[str, Any]] = []

    for recorded in recorder.turns:
        output = recorded.output or {
            "text": "",
            "tool_calls": [],
            "blocked": False,
            "finish_reason": "error",
            "usage": {},
        }
        actions: list[dict[str, Any]] = []
        for call in output.get("tool_calls", []):
            cid = call.get("call_id") or ""
            if cid and by_id.get(cid):
                hop = by_id[cid].pop(0)
            elif not cid and unkeyed:
                hop = unkeyed.pop(0)
            else:
                hop = None          # 절단·중단된 호출 — 판정이 없었음을 그대로 보인다
            after = _jsonable(hop.tool_result_preview) if hop is not None else None
            raw = (_jsonable(hop.tool_result_raw)
                   if include_raw and hop is not None and hop.tool_result_raw is not None
                   else None)
            # ★'실행 안 됨' 을 전부 '차단' 으로 뭉치면 안 된다★ — loop.py 는 실행 직전에
            # gate_decision='ALLOW' 를 확정하고, 도구가 예외를 던지면 executed 만 False 로
            # 남긴다. 표시 계층이 !executed 를 차단으로 읽으면 ★게이트가 막지 않은 것을
            # 막았다고 말하게 된다★(CLI 에 "ALLOW → 차단" 이라는 자기모순 줄이 찍혔다).
            note = str(getattr(hop, "note", "") or "")
            if hop is None:
                outcome = "unknown"          # 절단·중단되어 판정 자체가 없었던 호출
            elif hop.executed:
                outcome = "executed"
            elif hop.gate_decision in ("BLOCK", "HITL_DENY"):
                outcome = "blocked"          # 게이트가 실제로 막음
            elif note.startswith("tool_error"):
                outcome = "error"            # 게이트는 통과, 도구 실행이 실패
            else:
                outcome = "blocked"
            actions.append({
                "outcome": outcome,
                "tool": call.get("name", ""),
                "call_id": call.get("call_id", ""),
                "args": call.get("args", {}),
                "attempted": bool(hop.attempted) if hop is not None else True,
                "executed": bool(hop.executed) if hop is not None else False,
                "decision": hop.gate_decision if hop is not None else "NA",
                "rule": hop.gate_rule if hop is not None else "",
                "reason": hop.note if hop is not None else "홉 트레이스 없음",
                "taint_sources": list(hop.taint_sources) if hop is not None else [],
                "rail_scores": dict(hop.rail_scores) if hop is not None else {},
                "raw_tool_result": raw,
                "model_visible_result": after,
                "dlp_tokens": _tokens(after),
            })
        latest = recorded.messages[-1] if recorded.messages else {
            "role": "", "text": "", "tool_calls": [], "tool_results": []
        }
        turns.append({
            "turn": recorded.turn,
            "messages": recorded.messages,
            "latest_input": latest,
            "model_output": output,
            "provider_error": recorded.error,
            "actions": actions,
        })

    first_user = ""
    if recorder.turns:
        for message in recorder.turns[0].messages:
            if message.get("role") == "user":
                first_user = str(message.get("text", ""))
                break

    blocked = sum(1 for h in tool_hops
                  if h.attempted and not h.executed and h.gate_decision in ("BLOCK", "HITL_DENY"))
    errored = sum(1 for h in tool_hops
                  if h.attempted and not h.executed and h.gate_decision not in ("BLOCK", "HITL_DENY"))
    return {
        "meta": {
            "episode_id": episode.episode_id,
            "scenario": episode.scenario,
            "provider_mode": recorder.mode,
            "provider": provider_label,
            "model": model,
            "rail": rail_name,
            "defenses": sorted(k for k, v in defenses.items() if v),
            "scripted": recorder.mode == "scripted",
            "scripted_notice": (
                "고정된 도구 호출을 실제 런타임에 재생합니다. 모델 추론 증거가 아닙니다."
                if recorder.mode == "scripted" else ""
            ),
        },
        "input": {
            "original": episode.user_input,
            "provider_visible": first_user,
            "changed_by_before_model": bool(first_user and first_user != episode.user_input),
        },
        "summary": {
            "finish_reason": episode.finish_reason,
            "final_text": episode.final_text,
            "hops": len(tool_hops),
            "attempted": sum(1 for h in tool_hops if h.attempted),
            "executed": sum(1 for h in tool_hops if h.executed),
            "blocked": blocked,
            "errored": errored,
            "latency_ms": episode.total_latency_ms,
            "pending_approval": episode.pending_approval,
        },
        "turns": turns,
        "audit": audit or {},
    }


def format_workflow_text(document: dict[str, Any]) -> str:
    """터미널 녹화용 상세 워크플로 포맷."""

    meta = document["meta"]
    summary = document["summary"]
    lines = [
        "",
        "╔══ 전체 Agent Workflow ══════════════════════════════════════╗",
        f"  provider={meta['provider']} · model={meta['model']} · rail={meta['rail']}",
        f"  defenses={meta['defenses'] or ['none']}",
    ]
    if meta.get("scripted_notice"):
        lines.append(f"  ⚠️ {meta['scripted_notice']}")
    lines += [
        "╠══ ① INPUT / before_model ═══════════════════════════════════╣",
        f"  원본 입력      : {document['input']['original']}",
        f"  모델 표시 입력 : {document['input']['provider_visible']}",
    ]

    for turn in document["turns"]:
        latest = turn["latest_input"]
        lines.append(f"╠══ TURN {turn['turn']} · MODEL INPUT → OUTPUT ═══════════════════════╣")
        if latest.get("role") == "tool":
            for result in latest.get("tool_results", []):
                lines.append(
                    f"  [MODEL INPUT/tool] {result['name']} → {_compact(result['content'])}"
                )
        else:
            lines.append(f"  [MODEL INPUT/{latest.get('role')}] {latest.get('text', '')}")

        output = turn["model_output"]
        if output.get("tool_calls"):
            for action in turn["actions"]:
                source = "SCRIPTED ACTION" if meta.get("scripted") else "MODEL TOOL_CALL"
                lines.append(f"  [{source}] {action['tool']}({_compact(action['args'])})")
                verdict = {
                    "executed": "ALLOW → 실행",
                    "blocked": f"{action['decision']} → 차단",
                    "error": f"{action['decision']} → 실행 실패(도구 오류)",
                }.get(action["outcome"], f"{action['decision']} → 판정 없음")
                rule = f" · rule={action['rule']}" if action["rule"] else ""
                lines.append(f"  [before_tool] {verdict}{rule}")
                if action.get("raw_tool_result") is not None:
                    lines.append(f"  [TOOL/raw·합성] {_compact(action['raw_tool_result'])}")
                if action.get("model_visible_result") is not None:
                    lines.append(
                        f"  [after_tool→MODEL] {_compact(action['model_visible_result'])}"
                    )
                if action.get("dlp_tokens"):
                    lines.append(f"  [DLP] tokens={action['dlp_tokens']}")
                if action.get("rail_scores"):
                    lines.append(f"  [RAIL/log-only] {action['rail_scores']}")
                if action.get("reason"):
                    lines.append(f"  [NOTE] {action['reason']}")
        else:
            lines.append(f"  [MODEL FINAL] {output.get('text', '')}")
        if turn.get("provider_error"):
            lines.append(f"  [PROVIDER ERROR] {turn['provider_error']}")

    lines += [
        "╠══ RESULT ═══════════════════════════════════════════════════╣",
        f"  finish={summary['finish_reason']} · attempted={summary['attempted']} "
        f"· executed={summary['executed']} · blocked={summary['blocked']} "
        f"· latency={summary['latency_ms']}ms",
        f"  최종: {summary['final_text']}",
        "╚══════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)


def _compact(value: Any, limit: int = 900) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit - 1] + "…"
