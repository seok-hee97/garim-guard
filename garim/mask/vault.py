"""In-memory scoped token vault; raw values never appear in result serialization or repr."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from garim.mask.josa import correct_particle
from garim.models import Finding

_TOKEN = re.compile(
    # 번호는 9자리까지 — ★ 6차 검토: `<PHONE_111…(4,400자리)>` 가 `int()` 한도를 넘어 검사가 멈췄다.
    r"<\s*([A-Z][A-Z0-9_]*)\s*_\s*(\d{1,9})\s*>(이|가|을|를|은|는|과|와|으로|로)?",
    re.IGNORECASE,
)


# 조사 바로 뒤에 붙어 조사로 읽히게 하는 음절 — `과의` · `과도` · `로만` · `로는` · `로서` · `은요`.
_PARTICLE_CONTINUATIONS = frozenset("의도만요는서써")


def _coreference_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(slots=True)
class Vault:
    scope: str = "session"
    entries: dict[str, str] = field(default_factory=dict, repr=False)
    counters: dict[str, int] = field(default_factory=dict, repr=False)
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    # 입력에 원래 있던 토큰 모양 글자 — 복원하지 않고, "없는 토큰" 으로 세지도 않는다.
    _literals: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.scope not in {"task", "session"}:
            raise ValueError("vault scope must be 'task' or 'session'")

    def __repr__(self) -> str:
        return f"Vault(scope={self.scope!r}, entries=<redacted:{len(self.entries)}>)"

    def reserve_literals(self, text: str) -> None:
        """입력에 이미 있는 `<PHONE_1>` 같은 글자보다 큰 번호부터 쓴다.

        ★ 5차 검토 — `예시 <PHONE_1> … 010-1234-5678` 에서 실제 번호도 `<PHONE_1>` 이 되어
        상류가 같은 토큰 둘을 받았고, 응답을 복원하면 예시 자리까지 실제 번호로 바뀌었다.
        """
        for match in _TOKEN.finditer(text):
            label, number = match.group(1).upper(), int(match.group(2))
            canonical = f"<{label}_{number}>"
            if canonical not in self.entries:
                self._literals.add(canonical)
                self.counters[label] = max(self.counters.get(label, 0), number)

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
                missing = missing or canonical not in self._literals
                return match.group(0)
            particle = match.group(3)
            following = text[match.end() : match.end() + 1]
            syllable_follows = bool(following) and 0xAC00 <= ord(following) <= 0xD7A3
            # `<X_1>이에요`·`<X_1>이고` 의 `이` 는 주격 조사가 아니라 서술격이다.
            # 주격으로 보고 바꾸면 `김철수가에요` 가 된다 — 뒤에 음절이 이어지면 두지 않는다.
            copula = particle in {"이", "가"} and syllable_follows
            # ★ 다른 조사도 뒤에 음절이 이어지면 대개 낱말의 첫 글자다 — `<PERSON_1>과장님` 이
            #   `김철수와장님` 이, `은행원` 이 `는행원` 이 됐다(5차 검토). 조사 뒤에 붙는 음절
            #   (`과의` · `로는` · `은요` …)일 때만 교정한다.
            word = syllable_follows and following not in _PARTICLE_CONTINUATIONS
            if particle and not copula and not word:
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
    """스팬을 토큰으로 바꾼다. 겹치는 스팬은 합집합 하나로 가린다(앞 탐지의 라벨).

    ★ 초판은 뒤에서부터 문자열을 잘라 붙여 탐지 수 × 길이만큼 복사했고(제곱), 겹치는 스팬이
    오면 원문 글자가 남거나 토큰이 깨졌으며, 번호가 뒤에서부터 매겨졌다(`<EMAIL_2>` 가 앞).
    """
    regions: list[tuple[int, int, str]] = []
    for finding in sorted(findings, key=lambda item: (item.span[0], -item.span[1])):
        start, end = finding.span
        if not 0 <= start < end <= len(text):
            raise ValueError(f"mask span outside source: {finding.span}")
        if regions and start < regions[-1][1]:
            first, last, label = regions[-1]
            regions[-1] = (first, max(end, last), label)
            continue
        regions.append((start, end, labels.get(finding.code, "SENSITIVE")))
    pieces: list[str] = []
    cursor = 0
    for start, end, label in regions:
        pieces.append(text[cursor:start])
        pieces.append(vault.token_for(text[start:end], label))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
