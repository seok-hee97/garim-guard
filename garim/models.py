"""Stable input/output contracts shared by all layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

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
    rule_version: str = "0.1.0"
    stream: str = field(default="raw", repr=False, compare=False)

    @property
    def is_attack(self) -> bool:
        return self.code.startswith("A-")

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("stream", None)
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
