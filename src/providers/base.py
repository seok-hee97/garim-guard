"""생성 provider 계약.

게이트웨이는 이 `Provider.generate()` 시그니처에만 의존한다.
Gemini(기본)와 OpenAI(대체/비교)를 동일 계약으로 스왑한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GenConfig:
    """한 번의 생성 호출에 필요한 모든 파라미터.

    provider 별 세부 필드는 각 구현이 해석한다. 게이트웨이는 이 값을
    `config/policy.yaml`의 `gen` 섹션에서 그대로 만들어 넘긴다.
    """

    provider: str  # "gemini" | "openai"
    model: str
    system_instruction: str = ""
    # Gemini 4개 HarmCategory 공통 임계값. ⚠️ 2.5/3 기본 OFF → 반드시 지정.
    safety_threshold: str = "BLOCK_MEDIUM_AND_ABOVE"
    max_output_tokens: int = 1024
    temperature: float = 0.2
    # 구조화 출력이 필요할 때 Pydantic 모델(또는 JSON 스키마)을 넣는다.
    response_schema: Any | None = None


@dataclass
class GenResult:
    """생성 결과 + 안전 차단 정보(계약 고정 필드)."""

    text: str
    blocked: bool  # 입력/출력이 안전 필터로 막혔는가
    block_info: dict[str, Any] = field(default_factory=dict)
    raw: Any = None  # provider 원응답 (디버깅용, 로그에 PII 노출 주의)


@runtime_checkable
class Provider(Protocol):
    name: str

    async def generate(self, text: str, cfg: GenConfig) -> GenResult: ...


def build_provider(name: str) -> Provider:
    """이름으로 provider 인스턴스를 만든다 (지연 임포트로 선택 의존성 격리)."""

    key = (name or "gemini").lower()
    if key == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider()
    if key == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider()
    raise ValueError(f"알 수 없는 provider: {name!r} (gemini|openai)")
