"""OpenAIProvider — 동일 계약(base.py)의 OpenAI 구현.

Gemini 스왑용 겸, 무료 Gemini 5 RPM 병목의 녹화 대체 경로.
OpenAI 는 thought_signature 이슈가 없고 tool_call 에 실제 id 가 있어
왕복이 단순하다.

핵심 계약:
 1. 응답의 **모든** tool_calls 를 순회한다 (병렬 호출 보존).
 2. automatic_function_calling 개념이 없으므로 항상 수동 — cfg.afc=True 여도 안전하나
    계약 일관성을 위해 True 면 ValueError.
 3. OpenAI 는 Gemini 식 safety_settings 가 없다 (모더레이션은 별도 엔드포인트).
    cfg.safety_threshold 는 무시하고 그 사실을 note 로 남긴다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import GenConfig, GenResult, Msg, ToolCall, ToolSpec


def _key() -> str:
    v = os.getenv("OPENAI_API_KEY", "").strip()
    if not v:
        raise RuntimeError("OPENAI_API_KEY 가 없다 (.env 확인)")
    return v


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=_key())

    def _tools(self, specs: list[ToolSpec]) -> list[dict] | None:
        if not specs:
            return None
        return [
            {"type": "function",
             "function": {"name": s.name, "description": s.description,
                          "parameters": s.parameters}}
            for s in specs
        ]

    def _to_messages(self, messages: list[Msg], cfg: GenConfig) -> list[dict]:
        out: list[dict] = []
        if cfg.system_instruction:
            out.append({"role": "system", "content": cfg.system_instruction})
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "model":
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {"id": c.call_id, "type": "function",
                         "function": {"name": c.name,
                                      "arguments": json.dumps(c.args, ensure_ascii=False)}}
                        for c in m.tool_calls
                    ]
                out.append(msg)
            elif m.role == "tool":
                for r in m.tool_results:
                    out.append({
                        "role": "tool", "tool_call_id": r.call_id,
                        "content": json.dumps(_safe(r.content), ensure_ascii=False),
                    })
        return out

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        if cfg.automatic_function_calling:
            raise ValueError("automatic_function_calling 은 반드시 False (계약 일관성)")

        kwargs: dict[str, Any] = dict(
            model=cfg.model,
            messages=self._to_messages(messages, cfg),
            temperature=cfg.temperature,
            max_completion_tokens=cfg.max_output_tokens,
        )
        tools = self._tools(cfg.tools)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if cfg.response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        return self._parse(resp)

    def _parse(self, resp: Any) -> GenResult:
        choice = resp.choices[0]
        msg = choice.message
        finish = choice.finish_reason or ""

        calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):   # ★ 전부 순회 ★
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(name=tc.function.name, args=args, call_id=tc.id))

        usage = getattr(resp, "usage", None)
        return GenResult(
            text=msg.content or "",
            tool_calls=calls,
            blocked=finish == "content_filter",
            block_info={"finish_reason": finish} if finish == "content_filter" else {},
            finish_reason=finish or ("tool_calls" if calls else "stop"),
            usage={
                "prompt": getattr(usage, "prompt_tokens", 0) or 0,
                "total": getattr(usage, "total_tokens", 0) or 0,
            } if usage else {},
            raw=resp,
        )


def _safe(x: Any) -> Any:
    try:
        json.dumps(x, ensure_ascii=False)
        return x
    except TypeError:
        return str(x)
