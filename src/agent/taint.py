"""Tier B — 오염 추적 (taint provenance).

비신뢰 도구 출력을 Tainted(value, sources) 로 래핑하고, 파생값에 sources 를
합집합 전파한다. 싱크 인자의 .sources 가 비어 있지 않으면 게이트가 차단한다.

보장하는 성질: 비신뢰 데이터가 제어흐름이나 싱크를 고를 수 없다.
데이터로 흐르는 것은 허용하되, 무엇을 할지 결정하는 자리에는 오지 못한다.

⚠️ 선행연구: ARGUS(arXiv 2605.03378)가 taint provenance 를 이미 발표했다.
   본 과제는 신규성을 주장하지 않고 한국어·도구채널로 이식한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tainted:
    """오염된 값. sources 는 이 값에 영향을 준 비신뢰 출처의 집합."""

    value: Any
    sources: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.sources, frozenset):
            object.__setattr__(self, "sources", frozenset(self.sources))


def sources_of(x: Any) -> frozenset[str]:
    """임의 값에서 taint 출처를 수집한다 (중첩 구조 재귀)."""
    if isinstance(x, Tainted):
        return x.sources | sources_of(x.value)
    if isinstance(x, dict):
        out: frozenset[str] = frozenset()
        for k, v in x.items():
            out = out | sources_of(k) | sources_of(v)
        return out
    if isinstance(x, (list, tuple, set, frozenset)):
        out = frozenset()
        for v in x:
            out = out | sources_of(v)
        return out
    return frozenset()


def unwrap(x: Any) -> Any:
    """Tainted 껍질을 벗겨 순수 값을 얻는다 (중첩 포함). 게이트 통과 후 실행용."""
    if isinstance(x, Tainted):
        return unwrap(x.value)
    if isinstance(x, dict):
        return {unwrap(k): unwrap(v) for k, v in x.items()}
    if isinstance(x, list):
        return [unwrap(v) for v in x]
    if isinstance(x, tuple):
        return tuple(unwrap(v) for v in x)
    return x


def is_tainted(x: Any) -> bool:
    """이 값(또는 그 일부)이 비신뢰 출처의 영향을 받았는가."""
    return bool(sources_of(x))
