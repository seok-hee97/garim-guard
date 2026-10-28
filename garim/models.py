"""Stable input/output contracts shared by all layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from garim._version import __version__

if TYPE_CHECKING:
    from garim.mask.vault import Vault

Origin = Literal["system", "user", "retrieved", "tool"]
ContentType = Literal["prose", "code", "structured", "log", "unknown"]
Verdict = Literal["allow", "mask", "warn", "block"]


@dataclass(slots=True)
class Segment:
    text: str
    origin: Origin = "user"
    content_type: ContentType = "unknown"

    def __post_init__(self) -> None:
        if self.origin not in {"system", "user", "retrieved", "tool"}:
            raise ValueError(f"unsupported origin: {self.origin}")
        if self.content_type not in {"prose", "code", "structured", "log", "unknown"}:
            raise ValueError(f"unsupported content_type: {self.content_type}")


@dataclass(slots=True)
class Finding:
    code: str
    span: tuple[int, int]
    confidence: float
    layer: str
    rule: str
    segment_idx: int = 0
    evasion: list[str] = field(default_factory=list)
    normalized_only: bool = False
    decoded_from: tuple[int, int] | None = None
    # 규칙은 패키지와 함께 배포된다 — 규칙셋 버전 = 배포 버전.
    rule_version: str = __version__
    stream: str = field(default="raw", repr=False, compare=False)
    # 겹침 병합에서 mask 모드 탐지를 흡수했다 — 채택된 코드가 warn 이어도 이 스팬은 가린다.
    mask_required: bool = field(default=False, repr=False, compare=False)

    @property
    def is_attack(self) -> bool:
        return self.code.startswith("A-")

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("stream", None)
        data.pop("mask_required", None)
        return data


@dataclass(slots=True)
class CheckResult:
    verdict: Verdict
    leaks: list[Finding]
    attacks: list[Finding]
    masked_text: str | None
    masked_segments: list[str] | None
    vault: Vault | None
    normalization_delta: dict[str, int]
    content_types: list[str]
    degraded: bool
    latency_ms: dict[str, float]
    # ★ §3.6 — bool 하나로는 무엇이 degraded 인지 호출자가 알 수 없다.
    degraded_reasons: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view that deliberately excludes vault contents."""
        return {
            "verdict": self.verdict,
            "leaks": [finding.public_dict() for finding in self.leaks],
            "attacks": [finding.public_dict() for finding in self.attacks],
            "masked_text": self.masked_text,
            "masked_segments": self.masked_segments,
            "vault": None if self.vault is None else {"scope": self.vault.scope},
            "normalization_delta": dict(self.normalization_delta),
            "content_types": list(self.content_types),
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "latency_ms": dict(self.latency_ms),
        }
