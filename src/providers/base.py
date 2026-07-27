"""생성 provider 계약 (v2).

도구를 호출하는 에이전트 루프가 이 계약 위에서 돈다. v1 계약은 `generate(text: str)`
시그니처와 `tool_calls` 없는 `GenResult` 때문에 ReAct 루프를 표현할 수 없었다.
v2가 반드시 만족해야 하는 것:

1. **대화 이력** — `generate(messages, ...)`. 도구 결과를 다음 턴에 되먹여야 하므로
   단일 문자열로는 불가능하다.
2. **함수 호출 표현** — `GenResult.tool_calls`. 모델이 도구를 부르려 한다는 사실 자체를
   계약이 담지 못하면 게이트가 검사할 대상이 없다.
3. **병렬 호출 보존** — 모델은 한 턴에 여러 functionCall part를 낼 수 있다. 첫 part만
   읽으면 공격을 조용히 놓친다. 구현체는 **모든 part를 순회**해야 한다.
4. **AFC 비활성** — provider가 도구를 대신 실행해버리면 `before_tool` seam이 발동할 기회
   자체가 없다. `GenConfig.automatic_function_calling`은 항상 False다.

Gemini(기본) · OpenAI(대체) · Fake(카세트 재생)를 동일 계약으로 스왑한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "model", "tool"]


# ─────────────────────────────────────────────────────────────────────────
# 도구 선언 / 호출 / 결과
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ToolSpec:
    """모델에 넘기는 함수 선언. provider별 스키마로 각 구현이 변환한다."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (type=object)


@dataclass
class ToolCall:
    """모델이 요청한 도구 호출 1건.

    `call_id`는 이 호출과 그 결과를 짝짓는다. Gemini는 호출 id를 주지 않으므로
    구현체가 `{name}#{index}` 형태로 합성한다 — 병렬 호출에서 결과가 뒤섞이지 않도록.
    """

    name: str
    args: dict[str, Any]
    call_id: str


@dataclass
class ToolResult:
    """도구 실행 결과. 다음 턴 메시지로 되먹인다."""

    call_id: str
    name: str
    content: Any
    is_error: bool = False


@dataclass
class Msg:
    """대화 1턴. (provider 중립 표현 — 각 구현이 자기 API 규약으로 변환한다)

    - role="user"  : 사용자 발화 (text)
    - role="model" : 모델 응답 (text + tool_calls)
    - role="tool"  : 도구 결과 (tool_results). **도구 결과 재주입의 정본은 이것 하나다.**
                     구현체가 provider 규약으로 매핑한다 — 예: Gemini 는 이를
                     function_response part 를 담은 role="user" Content 로 변환한다.
                     루프 코드는 이 매핑을 알 필요가 없다 (그것이 추상화의 목적).
    """

    role: Role
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # provider 원본 passthrough. Gemini 3.x 는 functionCall 을 되돌려줄 때
    # 원래 딸려온 thought_signature 를 보존해야 하므로, 모델 턴의 원본 Content 를
    # 여기 실어 다음 요청에서 그대로 재전송한다. (카세트 키에는 포함되지 않는다)
    raw: Any = None


# ─────────────────────────────────────────────────────────────────────────
# 설정 / 결과
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class GenConfig:
    """한 번의 생성 호출에 필요한 모든 파라미터.

    값은 `config/policy.yaml`에서 주입한다. 코드에 하드코딩 금지.
    """

    provider: str  # "gemini" | "openai" | "fake"
    model: str
    system_instruction: str = ""
    tools: list[ToolSpec] = field(default_factory=list)
    # ⚠️ Gemini 2.5+는 안전 필터 기본 OFF → 4개 HarmCategory에 명시적으로 지정해야 한다.
    safety_threshold: str = "BLOCK_MEDIUM_AND_ABOVE"
    # 반드시 False. True면 provider가 도구를 대신 실행해 before_tool 게이트가 무력화된다.
    automatic_function_calling: bool = False
    # 격리 Q-LLM의 구조화 출력용 (Pydantic 모델 또는 JSON 스키마).
    response_schema: Any | None = None
    max_output_tokens: int = 1024
    temperature: float = 0.2


@dataclass
class GenResult:
    """생성 결과.

    `tool_calls`가 비어 있고 `text`만 있으면 에이전트 루프의 종료 조건이다.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    blocked: bool = False  # 안전 필터에 막혔는가
    block_info: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""  # stop | max_tokens | safety | tool_calls | ...
    usage: dict[str, int] = field(default_factory=dict)  # prompt/candidates/total tokens
    raw: Any = None  # provider 원응답 (⚠️ PII 포함 가능 — 로깅 금지)

    @property
    def wants_tools(self) -> bool:
        """루프 계속 조건."""
        return bool(self.tool_calls)


# ─────────────────────────────────────────────────────────────────────────
# Provider 계약
# ─────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Provider(Protocol):
    name: str

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        """대화 이력을 받아 한 턴을 생성한다.

        구현 계약:
        - 응답의 **모든** functionCall part를 `tool_calls`에 담는다 (첫 개만 읽지 말 것).
        - `cfg.automatic_function_calling`이 True면 `ValueError`를 던진다.
        - 안전 필터 차단 시 `blocked=True` + `block_info`를 채우고 예외를 던지지 않는다.
        """
        ...


def build_provider(name: str) -> Provider:
    """이름으로 provider 인스턴스를 만든다 (지연 임포트로 선택 의존성 격리)."""

    key = (name or "gemini").lower()
    if key == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider()
    if key == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider()
    if key == "fake":
        from .fake import FakeProvider

        return FakeProvider()
    raise ValueError(f"알 수 없는 provider: {name!r} (gemini|openai|fake)")
