"""4개 가드레일 seam 인터페이스.

before_* 에서 non-None 반환 = 단락(차단), None = 진행.
전 레일 fail-closed — 예외 시 통과가 아니라 차단.

D1 에서는 NoOpHooks(전부 패스스루)만 제공한다. Tier A/B/C·DLP 는 이후
이 프로토콜을 구현해 루프 코드 수정 없이 꽂힌다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..providers.base import GenResult, Msg, ToolCall, ToolResult


@dataclass
class Block:
    """before_model / before_tool 에서 반환하면 그 지점에서 단락된다."""

    rule: str                       # policy.yaml 의 단일 키 (트레이스 gate_rule)
    reason: str = ""
    decision: str = "BLOCK"         # BLOCK | HITL_DENY


@dataclass
class Redacted:
    """before_model 이 메시지를 비식별 변형해 돌려줄 때."""

    messages: list[Msg]


@dataclass
class Rehydrated:
    """before_tool 이 인자를 복원(내부 신뢰 도구)해 진행시킬 때."""

    args: dict[str, Any]


@dataclass
class Tainted_:
    """after_tool 이 출력을 래핑해 돌려줄 때 (taint/DLP)."""

    output: Any
    rail_scores: dict[str, float] = field(default_factory=dict)


class GuardrailHooks(Protocol):
    async def before_model(self, messages: list[Msg], ctx: Any) -> Redacted | Block | None: ...
    async def after_model(self, result: GenResult, ctx: Any) -> GenResult: ...
    async def before_tool(self, call: ToolCall, ctx: Any) -> Block | Rehydrated | None: ...
    async def after_tool(self, result: ToolResult, ctx: Any) -> Tainted_: ...


class NoOpHooks:
    """무방어 arm — 전부 패스스루. defenses.* 가 전부 false 인 D0 기준선.

    ⚠️ '무방어'는 안전하지 않다는 뜻이 아니라 '측정 기준선'이라는 뜻이다.
       이것이 있어야 방어층의 한계 기여를 잴 수 있다.
    """

    async def before_model(self, messages, ctx=None):
        return None

    async def after_model(self, result, ctx=None):
        return result

    async def before_tool(self, call, ctx=None):
        return None

    async def after_tool(self, result, ctx=None):
        return Tainted_(output=result.content)
