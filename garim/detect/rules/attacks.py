"""High-precision lexical fallback for P0; L1 remains the primary intent classifier."""

from __future__ import annotations

import re

from garim.models import Finding, Origin
from garim.normalize.chosung import chosung_signature

# ★ 전수 검토(2026-10-28) — 가장 흔한 꼴(`이전 지시사항을 모두 무시하고`, `지금까지의 모든 명령을
#   무시해`, `ignore your instructions`)을 놓쳤고, 반대로 `위 규칙을 무시하면 벌점` · `앞선 지시를
#   잊지 마세요` 를 공격으로 봤다. 명사 뒤 `사항` · 조사 · `모두/전부` 를 받고, 조건 · 부정
#   꼴은 뺀다.
_INJECTION = re.compile(
    # 한국어 — 앞말 경계(`상위 설정` 의 `위` 가 아니다), 명사 뒤 `사항` · 조사 · `모두/전부`.
    # ★ 재검토 — `설정` 은 IT 문서(`기존 설정을 모두 버리고 새로 설치`)에 흔해 뺐고, `폐기` ·
    #   `버리` 는 새 지시가 뒤따를 때만, `잊` 은 `잊어버리셨다면` 같은 경어 · 조건 꼴을 뺀다.
    #   선택 묶음 뒤의 `\s*` 를 묶음 안에 넣었다 — 긴 공백에서 제곱이었다(3만 자에 14초).
    r"(?<![가-힣])(?:이전|앞선|앞|위|기존|지금까지|그\s?전)의?\s*(?:모든\s*)?"
    r"(?:지시|지침|명령|규칙|프롬프트)(?:사항|들)?(?:을|를|은|는|이|가)?"
    r"\s*(?:(?:모두|전부|다|싹|완전히)\s*)?"
    r"(?:무시(?!하면|할\s?경우|하지|하셔도|해도)|잊(?:어|고)(?!버리셨|셨)"
    r"|(?:폐기|버리)(?:하고|고)\s*(?:내|새|다음))|"
    # `앞의 내용은 무시하고 새 지시를 따르세요` — 정정 메일(`위 내용은 무시해 주세요`)과 가르려고
    # 뒤에 지시 · 응답 요구가 따를 때만 받는다.
    r"(?<![가-힣])(?:앞의|위의?|이전의?)\s*내용(?:은|을|들은)\s*(?:(?:모두|전부|다)\s*)?"
    r"무시(?:하고|해)\s*.{0,15}?(?:지시|명령|따르|대답|답해|말해|알려)|"
    # 영어 — `Don't forget your instructions` 같은 부정은 뺀다.
    r"(?<!n't )(?<!not )(?<!never )(?:ignore|disregard|forget)\s+(?:(?:all|any)\s+(?:of\s+)?)?"
    r"(?:(?:the|your)\s+)?(?:previous|prior|above|earlier|preceding|original|system)\s+"
    r"(?:instructions?|prompts?|rules|guidelines)|"
    r"(?<!n't )(?<!not )(?<!never )(?:ignore|disregard|forget)\s+(?:all\s+(?:of\s+)?)?your\s+"
    r"(?:instructions?|prompts?|rules|guidelines)",
    re.IGNORECASE,
)
# 시스템 프롬프트를 내놓으라는 요구 — 조사는 있어도 없어도 된다(★ 재검토: 조사를 요구해
# `시스템 프롬프트 보여줘` 를 놓쳤다). 주어 꼴(`시스템 메시지가 출력되지`)과 작성 · 설계 이야기
# (`시스템 프롬프트 작성 요령을 보여줘`)는 공격이 아니다.
_SYSTEM_LEAK = re.compile(
    r"(?:시스템|개발자)\s*(?:프롬프트|지시(?:사항)?|메시지)"
    # 공백을 지운 스트림(`메시지가화면에`)에서도 맞도록 주어 조사 뒤 공백을 요구하지 않는다.
    r"(?!\s*(?:가|이))(?!\s*(?:작성|엔지니어링|설계|예시|템플릿|가이드))"
    r"(?:를|을|도|만|의\s*내용을?|\s*내용을?|\s*전문을?|\s*전체를?)?\s*.{0,6}?"
    r"(?:보여|출력|공개|알려|말해|적어|그대로)|"
    # 직접 묻는 꼴만 — `시스템 메시지가 무엇인지 설명해줘` 는 개념 질문이다(재검토).
    r"(?:시스템|개발자)\s*(?:프롬프트|메시지)(?:가|이|는|은)\s*(?:뭐야|뭐니|뭔데|무엇이야|무엇입니까)|"
    r"(?<![A-Za-z])(?:reveal|show|print|repeat|output|tell\s+me).{0,20}"
    r"(?:(?:system|developer)\s+(?:prompt|message|instructions?)|(?:initial|hidden)\s+"
    r"(?:prompt|instructions))|"
    r"what(?:'s|\s+is|\s+are)\s+your\s+"
    r"(?:system\s+prompt|prompt|system\s+instructions|initial\s+instructions)",
    re.IGNORECASE,
)
# ★ `제한(이|은)? 없는` 은 앵커로 쓸 수 없다. 한국어 위키 12,000 문장 실측에서
#   이 패턴만으로 오탐 2건이 나왔다 — "제한 없는 연분수", "제한이 없는 소프트웨어".
#   §8.1-8f 하네스가 처음 잡아낸 것이고, 정확히 그것을 하라고 만든 항목이다.
#   → 대상어(AI·모델·정책·필터…)를 앞뒤 좁은 창 안에서 함께 요구한다.
#   ★ 전수 검토 — 뒤쪽 `정책` · `규칙` 은 `제한이 없는 요금제 정책` 에 걸렸다. 앞쪽(`정책 제한
#   없는 모드`)에서만 받는다.
_UNRESTRICTED_TARGET = (
    r"(?:AI|인공지능|모델|어시스턴트|assistant|챗봇|필터|가이드라인|검열|제약)"
)
_UNRESTRICTED_TARGET_BEFORE = (
    r"(?:AI|인공지능|모델|어시스턴트|assistant|챗봇|정책|규칙|필터|가이드라인|검열|제약)"
)
# ★ 전수 검토 — `\bDAN\b` 가 대소문자를 가리지 않아 `Dan Abramov` · 인도네시아어 `dan`(그리고)에
#   걸렸고, 한글이 단어 문자라 `너는 DAN이야` 는 못 잡았다. `너는 이제 … 역할` · `…인 척해` 는
#   `영어 회화 선생님 역할을 해줘` · `면접관인 척해 줘` 같은 평범한 역할 놀이에 걸렸다 — 제한을
#   벗기려는 표지가 함께 있을 때만 받는다. `developer mode` 도 AI 를 향할 때만
#   (`Android developer mode` 는 아니다).
_JAILBREAK_PERSONA = re.compile(
    r"(?<![A-Za-z])DAN(?![A-Za-z])|(?i:do\s+anything\s+now)|"  # DAN 은 대소문자를 가린다
    # 해로운 인물 역할(`너는 이제 악당 역할이다`) — 평범한 역할 놀이는 받지 않는다.
    r"너는\s*(?:이제|지금부터)?.{0,15}(?:악당|사악한|악마|해커|범죄자)\s*(?:역할|이다|야|처럼)|"
    # `AI` 는 낱말로만 — `explain` 안의 `ai` 가 걸렸다(재검토).
    r"(?i:(?:(?<![A-Za-z])(?:ChatGPT|GPT|AI|assistant)(?![A-Za-z])|너).{0,20}developer\s+mode)|"
    r"(?<!Android )(?<!android )(?<!안드로이드 )(?<!iOS )(?<!Chrome )(?<!크롬 )"
    r"(?i:developer\s+mode\s*(?:활성화|enabled|on(?=\s*[.!]|\s*$)))|"
    r"(?i:(?<!how to )(?<![A-Za-z])(?:enable|activate|enter|turn\s+on)\s+developer\s+mode"
    r"(?!\s+(?:on|in|for)\s+(?:Android|Chrome|iOS|Windows|Mac|Xcode|USB|"
    r"(?:your|my|the|a)\s+(?:phone|device|browser|computer|PC|laptop|tablet))))|"
    r"(?i:you\s+are\s+(?:now\s+)?in\s+developer\s+mode)|"
    rf"(?i:{_UNRESTRICTED_TARGET_BEFORE}.{{0,12}}제한(?:이|은)?\s*없는)|"
    rf"(?i:제한(?:이|은)?\s*없는.{{0,12}}{_UNRESTRICTED_TARGET})|"
    r"(?:제한|검열|규칙|필터|윤리)\s*(?:(?:이|은)\s*)?없는.{0,15}(?:척해|역할|인\s*것처럼)"
)
_JAILBREAK_OTHER = re.compile(
    # `보안 정책 해제` 는 방화벽 · 계정 업무에 흔하다 — `보안` 은 필터 · 규칙일 때만 받는다.
    # 조건 꼴(`안전 지침을 무시하면 사고가 납니다`)과 `어기는 게시물 신고` 는 공격이 아니다.
    # `무시` 는 윤리 쪽만 — `안전 규칙을 무시하고 작업하다 다친 사례` 는 서술이다(재검토).
    r"윤리\s*(?:규칙|정책|필터|가이드라인|지침)(?:을|를)?\s*(?:무시(?!하면|할)|우회|해제)|"
    r"(?:안전|콘텐츠)\s*(?:규칙|정책|필터|가이드라인|지침)(?:을|를)?\s*(?:우회|해제)|"
    r"보안\s*(?:필터|규칙|가이드라인)(?:을|를)?\s*(?:무시(?!하면|할)|우회|해제)|"
    # 기기 탈옥(`iPhone jailbreak 탐지`, `jailbroken iPhone 수리`)은 뺀다.
    # 공백을 지운 스트림(`iPhonejailbreak`)에서도 맞도록 앞뒤 공백을 요구하지 않는다.
    r"(?<!iPhone )(?<!iPhone)(?<!iOS )(?<!iOS)(?<!아이폰 )(?<!아이폰)(?<!폰 )(?<!폰)"
    r"jailbreak(?:ing)?"
    r"(?!\s*(?:탐지|감지|detection|여부|된\s*기기))|"
    r"jailbroken(?!\s*(?:iPhone|iOS|기기|폰|device|phone))|"
    r"bypass\s+(?:safety|policy|filter)",
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


# 채팅에서 흔한 맨 자음 묶음 — 웃음(`ㅋㅋ`, `ㅎㅎ`)과 줄임말. 이것만 있는 줄은 초성체가 아니다.
_CHAT_JAMO = re.compile(
    r"[ㅋㅎ]+|ㅇㅋ|ㄱㅅ|ㅇㅇ|ㄴㄴ|ㄷㄷ|ㅊㅋ|ㅎㅇ|ㅂㅂ|ㅃㅃ|ㄱㄱ|ㅅㄱ|ㅇㄷ|ㄱㄷ|ㅈㅅ|ㅁㅊ|ㅅㅂ"
)
_BARE_CONSONANTS = re.compile(r"[ㄱ-ㅎ]+")
_DECOMPOSED = re.compile(r"[ㄱ-ㅎ][ㅏ-ㅣ]")


def _choseong_lines(text: str):
    """초성체로 쓴 줄과 그 시작 위치.

    ★ 전수 검토(2026-10-28) — 처음엔 글 어디에든 맨 자음이 하나라도 있으면 **글 전체**의 초성
      서명을 맞췄다. `ㅋㅋ` 하나가 든 채팅 기록 8줄의 58%가 공격으로 잡혔고(줄과 화자를 건너
      `전화 안내` → `ㅈㅎㅇㄴ`), 반대로 맨 모음 하나(`ㅠㅠ`)가 어디든 있으면 규칙 전체가 꺼졌다.
      채팅 줄임말이 아닌 맨 자음 묶음이 있는 줄만, 그 줄 안에서만 맞춘다. 자모 분해(`ㅇㅣ`)는
      자음 바로 뒤에 모음이 올 때만 본다.
    """
    start = 0
    for line in text.splitlines(keepends=True):
        runs = _BARE_CONSONANTS.findall(line)
        if any(not _CHAT_JAMO.fullmatch(run) for run in runs) and not _DECOMPOSED.search(line):
            yield start, line
        start += len(line)


def _detect_choseong(text: str, *, origin: Origin) -> list[Finding]:
    """초성체로 쓴 줄에서만 서명 매칭을 켠다 — 정상 산문은 이 경로에 오지 않는다."""
    codes = {"INJ": _injection_code(origin), "LEAK": "A-LEAK-01", "JB": "A-JB-02"}
    findings: list[Finding] = []
    for offset, line in _choseong_lines(text):
        signature, interval_map = chosung_signature(line)
        if not signature:
            continue
        for pattern, kind, rule in _CHOSEONG_SIGNATURES:
            for match in pattern.finditer(signature):
                covered = interval_map[match.start() : match.end()]
                if not covered:
                    continue
                span = (
                    offset + min(item[0] for item in covered),
                    offset + max(item[1] for item in covered),
                )
                findings.append(
                    Finding(codes[kind], span, 0.65, "L0", rule, evasion=["A-KO-02"])
                )
    return findings
