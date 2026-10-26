"""In-memory scoped token vault; raw values never appear in result serialization or repr."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from garim.mask.josa import correct_particle
from garim.models import Finding

_TOKEN = re.compile(
    r"<\s*([A-Z][A-Z0-9_]*)\s*_\s*(\d+)\s*>(이|가|을|를|은|는|과|와|으로|로)?",
    re.IGNORECASE,
)


def _coreference_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(slots=True)
class Vault:
    scope: str = "session"
    entries: dict[str, str] = field(default_factory=dict, repr=False)
    counters: dict[str, int] = field(default_factory=dict, repr=False)
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.scope not in {"task", "session"}:
            raise ValueError("vault scope must be 'task' or 'session'")

    def __repr__(self) -> str:
        return f"Vault(scope={self.scope!r}, entries=<redacted:{len(self.entries)}>)"

    def token_for(self, value: str, label: str) -> str:
        key = (label, _coreference_key(value))
        existing = self._reverse.get(key)
        if existing is not None:
            return existing
        sequence = self.counters.get(label, 0) + 1
        self.counters[label] = sequence
        token = f"<{label}_{sequence}>"
        self.entries[token] = value
        self._reverse[key] = token
        return token

    def restore(self, text: str) -> str:
        restored, _ = self.restore_with_status(text)
        return restored

    def restore_with_status(self, text: str) -> tuple[str, bool]:
        """Restore known tokens and report whether a vault-shaped token was missing."""

        missing = False

        def replace(match: re.Match[str]) -> str:
            nonlocal missing
            canonical = f"<{match.group(1).upper()}_{int(match.group(2))}>"
            value = self.entries.get(canonical)
            if value is None:
                missing = True
                return match.group(0)
            particle = match.group(3)
            following = text[match.end() : match.end() + 1]
            # `<X_1>이에요`·`<X_1>이고` 의 `이` 는 주격 조사가 아니라 서술격이다.
            # 주격으로 보고 바꾸면 `김철수가에요` 가 된다 — 뒤에 음절이 이어지면 두지 않는다.
            copula = (
                particle in {"이", "가"}
                and bool(following)
                and 0xAC00 <= ord(following) <= 0xD7A3
            )
            if particle and not copula:
                particle = correct_particle(value, particle)
            return value + (particle or "")

        return _TOKEN.sub(replace, text), missing


def mask_text(
    text: str,
    findings: list[Finding],
    *,
    vault: Vault,
    labels: dict[str, str],
) -> str:
    masked = text
    for finding in sorted(findings, key=lambda item: item.span[0], reverse=True):
        start, end = finding.span
        if not 0 <= start < end <= len(text):
            raise ValueError(f"mask span outside source: {finding.span}")
        value = text[start:end]
        token = vault.token_for(value, labels.get(finding.code, "SENSITIVE"))
        masked = masked[:start] + token + masked[end:]
    return masked
