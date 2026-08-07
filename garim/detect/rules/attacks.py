"""High-precision lexical fallback for P0; L1 remains the primary intent classifier."""

from __future__ import annotations

import re

from garim.models import Finding, Origin
from garim.normalize.chosung import chosung_signature, has_bare_chosung

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
# ★ `제한(이|은)? 없는` 은 앵커로 쓸 수 없다. 한국어 위키 12,000 문장 실측에서
#   이 패턴만으로 오탐 2건이 나왔다 — "제한 없는 연분수", "제한이 없는 소프트웨어".
#   §8.1-8f 하네스가 처음 잡아낸 것이고, 정확히 그것을 하라고 만든 항목이다.
#   → 대상어(AI·모델·정책·필터…)를 앞뒤 좁은 창 안에서 함께 요구한다.
_UNRESTRICTED_TARGET = (
    r"(?:AI|인공지능|모델|어시스턴트|assistant|챗봇|정책|규칙|필터|가이드라인|검열|제약)"
)
_JAILBREAK_PERSONA = re.compile(
    r"\bDAN\b|developer\s+mode|"
    rf"{_UNRESTRICTED_TARGET}.{{0,12}}제한(?:이|은)?\s*없는|"
    rf"제한(?:이|은)?\s*없는.{{0,12}}{_UNRESTRICTED_TARGET}|"
    r"너는\s*이제.{0,20}(?:이다|역할)|.{1,20}인\s*척해",
    re.IGNORECASE,
)
_JAILBREAK_OTHER = re.compile(
    r"(?:안전|보안|윤리)\s*(?:규칙|정책|필터)(?:을|를)?\s*(?:우회|해제)|jailbreak|bypass\s+(?:safety|policy|filter)",
    re.IGNORECASE,
)
# ★ 초성 서명 사전 — A-KO-02. 초성화는 가역이 아니므로 복원하지 않고 양쪽을 같은
#   초성 공간으로 내려 비교한다(`normalize/chosung.py`). 된소리는 평음으로 접혀
#   있으므로 패턴도 평음으로 쓴다: ㅍ→ㅂ, ㅌ→ㄷ, ㅊ→ㅈ, ㄲ→ㄱ.
#
#   초판은 `ㅁㅅㅎ|ㅇㅈㅈㅅ|ㅍㄹㅍㅌ` 의 **연속** 매칭이라 `ㅇㅈ ㅈㅅ는 ㅈ부 무시하고`
#   처럼 사이가 벌어진 실제 난독화를 전부 놓쳤다. 서명은 공백·조사를 흡수하지
#   못하므로 원 정규식과 같은 구조로 간격을 허용한다.
_CHOSEONG_SIGNATURES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # 이전/앞선 (모든) 지시/명령/규칙 (을/를) 무시/잊/폐기.
    # `위의`(ㅇㅇ)는 앵커가 너무 약해 뺀다 — `안녕하세요 ㅇㅈ` 의 `ㅅㅇ`+`ㅇㅈ` 에 걸린다.
    (
        re.compile(r"(?:ㅇㅈ|ㅇㅅ).{0,3}?(?:ㅈㅅ|ㅁㄹ|ㄱㅈ).{0,4}?(?:ㅁㅅ|ㅇ[ㄱㅈ]|ㅂㄱ)"),
        "INJ",
        "choseong_ignore",
    ),
    # 시스템/개발자 ... 프롬프트/지시 ... 보여/출력/공개
    (re.compile(r"(?:ㅅㅅㄷ|ㄱㅂㅈ).{0,4}?(?:ㅂㄹㅂㄷ|ㅈㅅ|ㅁㅅㅈ).{0,6}?(?:ㅂㅇ|ㅈㄹ|ㄱㄱ|ㄱㄷㄹ)"),
     "LEAK", "choseong_system_leak"),
    # 제한 없는 / 안전·보안 규칙 우회·해제
    (re.compile(r"ㅈㅎ.{0,2}?ㅇㄴ|(?:ㅇㅈ|ㅂㅇ|ㅇㄹ).{0,2}?(?:ㄱㅈ|ㅈㅊ|ㅂㄷ).{0,3}?(?:ㅇㅎ|ㅎㅈ)"),
     "JB", "choseong_jailbreak"),
)


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
    findings.extend(_detect_choseong(text, origin=origin))
    return findings


def _detect_choseong(text: str, *, origin: Origin) -> list[Finding]:
    """맨 초성 자모가 있을 때만 서명 매칭을 켠다 — 정상 산문은 이 경로에 오지 않는다."""
    if not has_bare_chosung(text):
        return []
    signature, interval_map = chosung_signature(text)
    if not signature:
        return []
    codes = {"INJ": _injection_code(origin), "LEAK": "A-LEAK-01", "JB": "A-JB-02"}
    findings: list[Finding] = []
    for pattern, kind, rule in _CHOSEONG_SIGNATURES:
        for match in pattern.finditer(signature):
            covered = interval_map[match.start() : match.end()]
            if not covered:
                continue
            span = (min(item[0] for item in covered), max(item[1] for item in covered))
            findings.append(
                Finding(codes[kind], span, 0.65, "L0", rule, evasion=["A-KO-02"])
            )
    return findings
