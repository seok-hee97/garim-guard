"""FakeProvider — 테스트 더블 겸 카세트 재생기.

두 모드:
  1. **script** — 미리 정한 GenResult 시퀀스를 순서대로 반환한다.
     API 없이 루프·게이트·트레이스를 결정적으로 테스트하는 데 쓴다.
  2. **cassette** — 실제 Gemini 응답을 녹화해 두고 키로 재생한다.
     5 RPM 무료 티어에서 애블레이션 전량 스윕을 돌리는 유일한 현실적 경로.

⚠️ script 모드로 만든 응답은 손저작이므로, 이걸로 만든 GenResult 를 카세트에
   저장할 때는 synthetic=True 로 표시된다 (무결성 규칙 1).
"""

from __future__ import annotations

import json
from typing import Any

from .base import GenConfig, GenResult, Msg, ToolCall
from .cassette import CassetteStore, cassette_key


def _msgs_to_contents(messages: list[Msg]) -> list[dict[str, Any]]:
    """카세트 키 계산용 — Msg 를 결정적 dict 로 직렬화."""
    out = []
    for m in messages:
        out.append({
            "role": m.role,
            "text": m.text,
            "tool_calls": [{"name": c.name, "args": c.args} for c in m.tool_calls],
            "tool_results": [{"name": r.name, "content": _safe(r.content)} for r in m.tool_results],
        })
    return out


def _safe(x: Any) -> Any:
    try:
        json.dumps(x, ensure_ascii=False, default=str)
        return x
    except TypeError:
        return str(x)


class FakeProvider:
    name = "fake"

    def __init__(self, *, script: list[GenResult] | None = None,
                 cassette: CassetteStore | None = None):
        self._script = list(script or [])
        self._i = 0
        self._cassette = cassette

    # 테스트 편의: (name, args) 튜플 목록으로 스크립트를 만든다
    @classmethod
    def scripted(cls, turns: list[Any]) -> "FakeProvider":
        """turns: 각 원소가 str(텍스트 종료) 또는 [(tool, args), ...] (도구 호출 턴)."""
        script = []
        for t in turns:
            if isinstance(t, str):
                script.append(GenResult(text=t, tool_calls=[], finish_reason="stop"))
            else:
                calls = [ToolCall(name=n, args=a, call_id=f"{n}#{i}")
                         for i, (n, a) in enumerate(t)]
                script.append(GenResult(text="", tool_calls=calls, finish_reason="tool_calls"))
        return cls(script=script)

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        if cfg.automatic_function_calling:
            raise ValueError("automatic_function_calling 은 반드시 False (before_tool 게이트 보호)")

        # 카세트 모드 우선
        if self._cassette is not None:
            key = cassette_key(cfg.model, _fingerprint(cfg), _msgs_to_contents(messages))
            entry = self._cassette.get(key)
            if entry is None:
                self._cassette.miss(key)  # on_miss=error 면 여기서 예외
            else:
                return _result_from_dict(entry)

        # 스크립트 모드
        if self._i >= len(self._script):
            # 스크립트 소진 → 안전하게 텍스트 종료
            return GenResult(text="(스크립트 종료)", tool_calls=[], finish_reason="stop")
        r = self._script[self._i]
        self._i += 1
        return r


def _fingerprint(cfg: GenConfig) -> str:
    return json.dumps({
        "sys": cfg.system_instruction,
        "tools": sorted(t.name for t in cfg.tools),
        "safety": cfg.safety_threshold,
        "temp": cfg.temperature,
    }, ensure_ascii=False, sort_keys=True)


def _result_from_dict(d: dict) -> GenResult:
    return GenResult(
        text=d.get("text", ""),
        tool_calls=[ToolCall(name=c["name"], args=c["args"], call_id=c["call_id"])
                    for c in d.get("tool_calls", [])],
        blocked=d.get("blocked", False),
        block_info=d.get("block_info", {}),
        finish_reason=d.get("finish_reason", ""),
        usage=d.get("usage", {}),
        raw={"replayed": True},
    )
