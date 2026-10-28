"""Small, dependency-free Hangul composition helpers."""

from __future__ import annotations

from garim.normalize.offsets import Interval, IntervalMap

L_START = 0x1100
V_START = 0x1161
T_START = 0x11A7
L_COUNT = 19
V_COUNT = 21
T_COUNT = 28
S_START = 0xAC00

_COMPAT_LEADS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_COMPAT_VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_COMPAT_TRAILS = "\0ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_LEAD_TO_TRAIL = {
    lead: _COMPAT_TRAILS.index(lead) for lead in _COMPAT_LEADS if lead in _COMPAT_TRAILS
}


def _lead_index(character: str) -> int | None:
    code = ord(character)
    if L_START <= code < L_START + L_COUNT:
        return code - L_START
    try:
        return _COMPAT_LEADS.index(character)
    except ValueError:
        return None


def _vowel_index(character: str) -> int | None:
    code = ord(character)
    if V_START <= code < V_START + V_COUNT:
        return code - V_START
    try:
        return _COMPAT_VOWELS.index(character)
    except ValueError:
        return None


def _trail_index(character: str) -> int | None:
    code = ord(character)
    if T_START < code < T_START + T_COUNT:
        return code - T_START
    if character in _COMPAT_TRAILS:
        return _COMPAT_TRAILS.index(character)
    lead = _lead_index(character)
    if lead is not None:
        return _LEAD_TO_TRAIL.get(_COMPAT_LEADS[lead])
    return None


def compose_jamo(text: str, interval_map: IntervalMap) -> tuple[str, IntervalMap, int]:
    output: list[str] = []
    mapped: IntervalMap = []
    composed = 0
    index = 0
    while index < len(text):
        lead = _lead_index(text[index])
        if lead is not None and index + 1 < len(text):
            vowel = _vowel_index(text[index + 1])
            if vowel is not None:
                consumed = 2
                trailing = 0
                if index + 2 < len(text):
                    candidate = _trail_index(text[index + 2])
                    next_is_vowel = (
                        index + 3 < len(text) and _vowel_index(text[index + 3]) is not None
                    )
                    if candidate is not None and not next_is_vowel:
                        trailing = candidate
                        consumed = 3
                syllable = S_START + lead * V_COUNT * T_COUNT
                syllable += vowel * T_COUNT + trailing
                source: list[Interval] = interval_map[index : index + consumed]
                output.append(chr(syllable))
                mapped.append((min(item[0] for item in source), max(item[1] for item in source)))
                composed += 1
                index += consumed
                continue
        trail = ord(text[index + 1]) - T_START if index + 1 < len(text) else 0
        code = ord(text[index])
        if S_START <= code <= 0xD7A3 and (code - S_START) % T_COUNT == 0 and 0 < trail < T_COUNT:
            # ★ 받침 없는 음절 + 조합형 받침(U+11A8~U+11C2). 화면에는 `이전` 으로 붙어 보이는
            #   `이저\u11ab` 가 규칙에 걸리지 않았다(5차 검토). 호환 자모(ㄴ)는 붙이지 않는다 —
            #   채팅의 `ㄴ`(아니) 같은 낱자를 음절로 만들면 뜻이 바뀐다.
            source = interval_map[index : index + 2]
            output.append(chr(code + trail))
            mapped.append((min(item[0] for item in source), max(item[1] for item in source)))
            composed += 1
            index += 2
            continue
        output.append(text[index])
        mapped.append(interval_map[index])
        index += 1
    return "".join(output), mapped, composed


def has_final_consonant(text: str) -> bool:
    if not text:
        return False
    code = ord(text[-1])
    return S_START <= code <= 0xD7A3 and (code - S_START) % T_COUNT != 0
