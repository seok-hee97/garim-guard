"""GeminiProvider — google-genai 위의 계약 구현.

핵심 계약 (base.py):
 1. 응답의 **모든** functionCall part 를 순회한다 (첫 개만 읽으면 병렬 호출을 놓친다).
 2. automatic_function_calling=True 면 ValueError (before_tool 게이트 보호).
 3. safety_settings 4개 HarmCategory 명시 (2.5+ 기본 OFF).
 4. 안전 차단은 예외가 아니라 blocked=True.
"""

from __future__ import annotations

import os
from typing import Any

from .base import GenConfig, GenResult, Msg, ToolCall, ToolSpec
from ._resilience import transient_retry

_HARM_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)


def _resolve_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.getenv(name, "").strip()
        if v:
            return v
    raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY 가 없다 (.env 확인)")


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        from google import genai  # 지연 임포트 — 선택 의존성 격리
        from google.genai import types

        self._genai = genai
        # 벽시계 타임아웃(ms) 주입 — 응답 지연이 요청을 무한정 붙잡지 못하게.
        self._client = genai.Client(
            api_key=_resolve_key(),
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

    def _tool_config(self, specs: list[ToolSpec]) -> Any:
        from google.genai import types

        if not specs:
            return None
        decls = [
            types.FunctionDeclaration(
                name=s.name, description=s.description, parameters=s.parameters
            )
            for s in specs
        ]
        return types.Tool(function_declarations=decls)

    def _to_contents(self, messages: list[Msg]) -> list[Any]:
        """Msg(provider 중립) → Gemini Content. 도구 결과는 user 역할 function_response."""
        from google.genai import types

        contents = []
        for m in messages:
            if m.role == "tool":
                # Gemini 규약: function_response 는 user 역할 턴에 담는다
                parts = [
                    types.Part(function_response=types.FunctionResponse(
                        name=r.name, response=_as_response(r.content)))
                    for r in m.tool_results
                ]
                contents.append(types.Content(role="user", parts=parts))
            elif m.role == "model":
                # ★ 원본 Content 를 그대로 재전송한다 (thought_signature 보존).
                #   Gemini 3.x 는 functionCall 을 되돌려줄 때 서명이 없으면 400 을 낸다.
                reused = _reuse_model_content(m.raw)
                if reused is not None:
                    contents.append(reused)
                    continue
                # 폴백(카세트 재생 등 원본이 없을 때) — 서명 없이 재구성
                parts = []
                if m.text:
                    parts.append(types.Part(text=m.text))
                for c in m.tool_calls:
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=c.name, args=c.args)))
                contents.append(types.Content(role="model", parts=parts))
            else:  # user
                contents.append(types.Content(role="user", parts=[types.Part(text=m.text)]))
        return contents

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        from google.genai import types

        if cfg.automatic_function_calling:
            raise ValueError("automatic_function_calling 은 반드시 False (before_tool 게이트 보호)")

        gcfg_kwargs: dict[str, Any] = dict(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            safety_settings=[
                types.SafetySetting(category=c, threshold=cfg.safety_threshold)
                for c in _HARM_CATEGORIES
            ],
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_output_tokens,
        )
        if cfg.system_instruction:
            gcfg_kwargs["system_instruction"] = cfg.system_instruction
        tool = self._tool_config(cfg.tools)
        if tool is not None:
            gcfg_kwargs["tools"] = [tool]
        if cfg.response_schema is not None:
            gcfg_kwargs["response_mime_type"] = "application/json"
            gcfg_kwargs["response_schema"] = cfg.response_schema

        @transient_retry()
        async def _call() -> Any:
            # aio: async 네이티브 — 이벤트루프 비블로킹(동시 요청 처리).
            return await self._client.aio.models.generate_content(
                model=cfg.model,
                contents=self._to_contents(messages),
                config=types.GenerateContentConfig(**gcfg_kwargs),
            )

        return self._parse(await _call())

    def _parse(self, resp: Any) -> GenResult:
        cand = (getattr(resp, "candidates", None) or [None])[0]
        finish = str(getattr(cand, "finish_reason", "")) if cand else ""
        blocked = finish.upper().endswith("SAFETY") or bool(
            getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        )

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        if cand and getattr(cand, "content", None) and cand.content.parts:
            # ★ 모든 part 순회 ★ — parts[0] 만 보면 병렬 호출을 조용히 놓친다
            for idx, part in enumerate(cand.content.parts):
                fc = getattr(part, "function_call", None)
                if fc:
                    calls.append(ToolCall(
                        name=fc.name, args=dict(fc.args or {}), call_id=f"{fc.name}#{idx}"))
                elif getattr(part, "text", None):
                    text_parts.append(part.text)

        usage = getattr(resp, "usage_metadata", None)
        return GenResult(
            text="".join(text_parts),
            tool_calls=calls,
            blocked=blocked,
            block_info={"finish_reason": finish} if blocked else {},
            finish_reason=finish or ("tool_calls" if calls else "stop"),
            usage={
                "prompt": getattr(usage, "prompt_token_count", 0) or 0,
                "total": getattr(usage, "total_token_count", 0) or 0,
            } if usage else {},
            raw=resp,
        )


def _as_response(content: Any) -> dict[str, Any]:
    """도구 결과를 Gemini FunctionResponse.response(dict)로 정규화한다."""
    if isinstance(content, dict):
        return content
    return {"result": content}


def _reuse_model_content(raw: Any) -> Any:
    """모델 턴의 원본 Content 를 꺼낸다 (thought_signature 보존).

    raw 는 GeminiProvider._parse 가 실어둔 GenerateContentResponse 다.
    카세트 재생 등으로 원본이 dict/None 이면 None 을 돌려 폴백 재구성하게 한다.
    """
    if raw is None or isinstance(raw, dict):
        return None
    cand = (getattr(raw, "candidates", None) or [None])[0]
    if cand is None:
        return None
    return getattr(cand, "content", None)
