"""High-precision lexical fallback for P0; L1 remains the primary intent classifier."""

from __future__ import annotations

import re

from garim.models import Finding, Origin

_INJECTION = re.compile(
    r"(?:이전|앞선|위의?)\s*(?:모든\s*)?(?:지시|명령|규칙)(?:을|를)?\s*(?:무시|잊|폐기)|"
    r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    re.IGNORECASE,
)
_SYSTEM_LEAK = re.compile(
    r"(?:시스템|개발자)\s*(?:프롬프트|지시|메시지).{0,16}(?:보여|출력|공개|그대로)|"
    r"(?:reveal|show|print|repeat).{0,20}(?:system|developer)\s+(?:prompt|message|instructions?)",
    re.IGNORECASE,
)
_JAILBREAK_PERSONA = re.compile(
    r"\bDAN\b|developer\s+mode|제한(?:이|은)?\s*없는|너는\s*이제.{0,20}(?:이다|역할)|.{1,20}인\s*척해",
    re.IGNORECASE,
)
_JAILBREAK_OTHER = re.compile(
    r"(?:안전|보안|윤리)\s*(?:규칙|정책|필터)(?:을|를)?\s*(?:우회|해제)|jailbreak|bypass\s+(?:safety|policy|filter)",
    re.IGNORECASE,
)
_CHOSEONG = re.compile(r"(?<![ㄱ-ㅎㅏ-ㅣ])(?:ㅁㅅㅎ|ㅇㅈㅈㅅ|ㅍㄹㅍㅌ)(?![ㄱ-ㅎㅏ-ㅣ])")


def _injection_code(origin: Origin) -> str:
    """A-INJ-01 직접 / A-INJ-02 간접 — DESIGN §2.5b 표."""
    return "A-INJ-02" if origin in ("retrieved", "tool") else "A-INJ-01"


def detect_attacks(text: str, *, origin: Origin = "user") -> list[Finding]:
    """★ origin 으로 탐지를 끄지 않는다 — DESIGN §2.5b.

    초판은 `origin == "system"` 이면 A-INJ-01·A-JB-01·초성 규칙을 무조건 배출하지
    않았다. 그런데 `origin` 은 호출자가 알려주는 **자기신고 필드**라(§3.2.1),
    라이브러리 모드에서 검색 결과를 `system` 으로 라벨링하는 호출자가 하나만
    있으면 인젝션 탐지가 통째로 꺼졌다.

    §2.5b 의 원시 연산은 "검사 ON/OFF" 가 아니라 **"검사는 항상 하고, 공격 축에서만
    코드 배출과 verdict 를 강등"** 이다. 강등은 정책층이 `trust_declared` 를 보고
    결정한다(`policy/engine.py`).
    """
    findings: list[Finding] = []
    for match in _INJECTION.finditer(text):
        findings.append(
            Finding(_injection_code(origin), match.span(), 0.85, "L0", "attack_keyword")
        )
    for match in _SYSTEM_LEAK.finditer(text):
        findings.append(Finding("A-LEAK-01", match.span(), 0.85, "L0", "system_leak_keyword"))
    for match in _JAILBREAK_PERSONA.finditer(text):
        findings.append(Finding("A-JB-01", match.span(), 0.8, "L0", "persona_keyword"))
    for match in _JAILBREAK_OTHER.finditer(text):
        findings.append(Finding("A-JB-02", match.span(), 0.8, "L0", "jailbreak_keyword"))
    for match in _CHOSEONG.finditer(text):
        findings.append(
            Finding(
                _injection_code(origin),
                match.span(),
                0.65,
                "L0",
                "choseong_dictionary",
                evasion=["A-KO-02"],
            )
        )
    return findings
