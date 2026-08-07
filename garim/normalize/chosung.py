"""초성체 서명 — A-KO-02.

초성화는 **가역이 아니다.** `ㅇㅈ` 은 이전·아직·어제·오전 어느 것도 될 수 있어
정규화 체인의 다른 일곱 단계처럼 원문을 복원할 수 없다. 그래서 §3.4 가 여기에
"초성 사전" 을 둔 것이고, 복원 대신 **양쪽을 같은 초성 공간으로 내려 비교**한다.

    이전 지시는 전부 무시하고   →  ㅇㅈㅈㅅㄴㅈㅂㅁㅅㅎㄱ
    ㅇㅈ ㅈㅅ는 ㅈ부 무시하고    →  ㅇㅈㅈㅅㄴㅈㅂㅁㅅㅎㄱ

부분 초성화(`ㅈ부`, `지ㄱ부ㅌ`)가 섞여도 서명은 같아진다. 벤치마크의 실제 난독화가
이 형태이며, 기존 `_CHOSEONG` 정규식이 `ㅁㅅㅎ|ㅇㅈㅈㅅ|ㅍㄹㅍㅌ` 세 개의 **연속**
패턴만 봐서 `ㅇㅈ ㅈㅅ는` 처럼 사이가 벌어진 형태를 전부 놓쳤다.

⚠️ 서명 매칭은 정보를 버리므로 오탐이 쉽다. **맨 초성 자모가 실제로 들어 있을 때만**
켠다(`has_bare_chosung`). 정상 한국어 산문에는 맨 `ㅇㅈ` 이 나오지 않는다.
"""

from __future__ import annotations

from garim.normalize.offsets import Interval, IntervalMap

_S_START = 0xAC00
_S_END = 0xD7A3
_V_COUNT = 21
_T_COUNT = 28
_COMPAT_LEADS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"

# 된소리·거센소리를 평음으로 접는다. `tensify` 난독화(`이쩐 찌시`)와 초성 표기
# 흔들림(`ㄲ`/`ㄱ`)을 같은 서명으로 모은다 — §2.2 의 은닉 축 하나가 여기서 닫힌다.
_FOLD = {
    "ㄲ": "ㄱ", "ㅋ": "ㄱ",
    "ㄸ": "ㄷ", "ㅌ": "ㄷ",
    "ㅃ": "ㅂ", "ㅍ": "ㅂ",
    "ㅆ": "ㅅ",
    "ㅉ": "ㅈ", "ㅊ": "ㅈ",
}


def fold_tense(lead: str) -> str:
    """된소리·거센소리 → 평음. 초성 서명 비교의 정규형."""
    return _FOLD.get(lead, lead)


_COMPAT_VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"


def has_bare_chosung(text: str) -> bool:
    """서명 매칭을 켜는 조건 — 맨 자음이 있고 맨 모음은 없어야 한다.

    ★ 모음 조건이 없으면 **자모 분해가 초성체로 오인된다.** `ㅇㅣㅈㅓㄴ ㅈㅣㅅㅣ` 에도
    맨 `ㅇ`·`ㅈ` 이 들어 있어 서명 규칙이 발화하고, A-KO-01 이어야 할 은닉 코드가
    A-KO-02 로 바뀐다. 초성화는 음절을 초성 하나로 줄이므로 **맨 모음을 만들지
    않는다** — 자모 분해와 갈리는 지점이 정확히 여기다. 분해된 쪽은 `compose_jamo`
    가 `norm` 스트림에서 원문으로 되돌리므로 이 경로가 필요 없다.
    """
    if any(character in _COMPAT_VOWELS for character in text):
        return False
    return any(character in _COMPAT_LEADS for character in text)


def chosung_signature(text: str) -> tuple[str, IntervalMap]:
    """한글에서만 초성을 뽑아 이어 붙이고, 각 초성의 원문 구간을 함께 준다.

    한글이 아닌 문자(공백·문장부호·라틴)는 서명에서 빠진다. 초성화는 음절 경계를
    무너뜨리므로 공백을 신호로 쓸 수 없기 때문이다.
    """
    signature: list[str] = []
    mapped: IntervalMap = []
    for index, character in enumerate(text):
        code = ord(character)
        if _S_START <= code <= _S_END:
            lead = _COMPAT_LEADS[(code - _S_START) // (_V_COUNT * _T_COUNT)]
        elif character in _COMPAT_LEADS:
            lead = character
        else:
            continue
        signature.append(fold_tense(lead))
        span: Interval = (index, index + 1)
        mapped.append(span)
    return "".join(signature), mapped


def signature_of(phrase: str) -> str:
    """사전 항목을 같은 서명 공간으로 내린다."""
    return chosung_signature(phrase)[0]


_LEAD_INDEX = {lead: index for index, lead in enumerate(_COMPAT_LEADS)}


def detensify(text: str) -> tuple[str, int]:
    """음절 초성의 된소리·거센소리를 평음으로 내린 문자열과 바뀐 음절 수.

    `이쩐 찌시는 전뿌` → `이전 지시는 전부`. 벤치마크의 `tensify` 난독화가 이 형태다.

    ⚠️ **손실 변환이다.** `짜다`→`자다`, `깨다`→`개다` 처럼 정상 낱말도 바뀐다.
    그래서 `norm` 본류에 넣지 않고 `nospace` 처럼 **별도 스트림**으로 두고, 거기서 나온
    Finding 은 신뢰도를 깎는다(§2.3 이 `norm_nospace` 에 쓴 것과 같은 처리다).
    길이가 보존되므로 구간 맵은 그대로 쓸 수 있다.
    """
    output: list[str] = []
    changed = 0
    for character in text:
        code = ord(character)
        if _S_START <= code <= _S_END:
            offset = code - _S_START
            lead_index = offset // (_V_COUNT * _T_COUNT)
            folded = fold_tense(_COMPAT_LEADS[lead_index])
            new_index = _LEAD_INDEX[folded]
            if new_index != lead_index:
                rest = offset % (_V_COUNT * _T_COUNT)
                output.append(chr(_S_START + new_index * _V_COUNT * _T_COUNT + rest))
                changed += 1
                continue
        output.append(character)
    return "".join(output), changed
