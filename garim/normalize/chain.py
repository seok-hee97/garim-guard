"""Seven-stage reversible normalization chain."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from garim.normalize.chosung import detensify
from garim.normalize.encoding import decoded_views
from garim.normalize.hangul import compose_jamo
from garim.normalize.offsets import IntervalMap, identity_map

DELTA_KEYS = (
    "nfkc",
    "zero_width",
    "tag_chars",
    "homoglyph",
    "jamo_composed",
    "hanja",
    "space",
    "b64_decoded",
)

_CONFUSABLE = {
    "Α": "A",
    "А": "A",
    "Β": "B",
    "В": "B",
    "Ε": "E",
    "Е": "E",
    "Η": "H",
    "І": "I",
    "Κ": "K",
    "М": "M",
    "Ν": "N",
    "О": "O",
    "Ρ": "P",
    "Р": "P",
    "С": "C",
    "Τ": "T",
    "Х": "X",
    "Υ": "Y",
    "а": "a",
    "е": "e",
    "і": "i",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
    "у": "y",
}
_HANJA = {
    "無": "무",
    "視": "시",
    "前": "전",
    "指": "지",
    "示": "시",
    "命": "명",
    "令": "령",
    "秘": "비",
    "密": "밀",
    "外": "외",
    "部": "부",
}
_HORIZONTAL_SPACE = re.compile(r"[ \t\u3000]+")


@dataclass(frozen=True, slots=True)
class NormalizedView:
    text: str
    interval_map: IntervalMap
    name: str
    decoded_from: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class NormalizedBundle:
    raw: NormalizedView
    norm: NormalizedView
    nospace: NormalizedView
    # ★ 된소리·거센소리를 평음으로 내린 스트림 — A-KO-05 옆자리의 은닉 축이다.
    #   `이쩐 찌시는 전뿌` 가 규칙에 걸리지 않던 것을 닫는다. 손실 변환이라
    #   `nospace` 와 같은 취급을 한다(별도 스트림 + 신뢰도 감쇄).
    detensified: NormalizedView
    decoded: list[NormalizedView] = field(default_factory=list)
    delta: dict[str, int] = field(default_factory=dict)


def _nfkc_charwise(text: str, source_map: IntervalMap) -> tuple[str, IntervalMap, int]:
    output: list[str] = []
    mapped: IntervalMap = []
    changed = 0
    for character, interval in zip(text, source_map, strict=True):
        if 0x3131 <= ord(character) <= 0x318E:
            normalized = character
        else:
            normalized = unicodedata.normalize("NFKC", character)
        if normalized != character:
            changed += 1
        output.extend(normalized)
        mapped.extend([interval] * len(normalized))
    return "".join(output), mapped, changed


def _drop_controls(text: str, source_map: IntervalMap) -> tuple[str, IntervalMap, int, int]:
    output: list[str] = []
    mapped: IntervalMap = []
    zero_width = 0
    tag_chars = 0
    for character, interval in zip(text, source_map, strict=True):
        code = ord(character)
        if 0xE0000 <= code <= 0xE007F:
            tag_chars += 1
            continue
        if 0x200B <= code <= 0x200F or code == 0xFEFF or 0x2060 <= code <= 0x2064:
            zero_width += 1
            continue
        output.append(character)
        mapped.append(interval)
    return "".join(output), mapped, zero_width, tag_chars


def _translate(
    text: str, source_map: IntervalMap, table: dict[str, str]
) -> tuple[str, IntervalMap, int]:
    output: list[str] = []
    mapped: IntervalMap = []
    changed = 0
    for character, interval in zip(text, source_map, strict=True):
        replacement = table.get(character, character)
        changed += replacement != character
        output.extend(replacement)
        mapped.extend([interval] * len(replacement))
    return "".join(output), mapped, changed


def _conservative_spaces(text: str, source_map: IntervalMap) -> tuple[str, IntervalMap]:
    output: list[str] = []
    mapped: IntervalMap = []
    index = 0
    while index < len(text):
        match = _HORIZONTAL_SPACE.match(text, index)
        if match:
            end = match.end()
            if end < len(text) and text[end] not in "\r\n":
                intervals = source_map[index:end]
                output.append(" ")
                mapped.append(
                    (min(item[0] for item in intervals), max(item[1] for item in intervals))
                )
            index = end
            continue
        output.append(text[index])
        mapped.append(source_map[index])
        index += 1
    return "".join(output), mapped


def normalize(text: str) -> NormalizedBundle:
    delta = {key: 0 for key in DELTA_KEYS}
    raw_map = identity_map(len(text))
    current, current_map, delta["nfkc"] = _nfkc_charwise(text, raw_map)
    current, current_map, delta["zero_width"], delta["tag_chars"] = _drop_controls(
        current, current_map
    )
    current, current_map, delta["homoglyph"] = _translate(current, current_map, _CONFUSABLE)
    current, current_map, delta["jamo_composed"] = compose_jamo(current, current_map)
    current, current_map, delta["hanja"] = _translate(current, current_map, _HANJA)
    current, current_map = _conservative_spaces(current, current_map)

    no_space_chars: list[str] = []
    no_space_map: IntervalMap = []
    for character, interval in zip(current, current_map, strict=True):
        if character.isspace():
            delta["space"] += 1
            continue
        no_space_chars.append(character)
        no_space_map.append(interval)

    # delta 에는 넣지 않는다 — L1 이 받는 8차원 계약(`NORMALIZATION_DELTA_ORDER`)이
    # 고정이고 `normalization_delta_vector` 가 추가 키를 거부한다.
    detensified_text, _ = detensify(current)

    decoded = decoded_views(current, current_map)
    delta["b64_decoded"] = len(decoded)
    return NormalizedBundle(
        raw=NormalizedView(text, raw_map, "raw"),
        norm=NormalizedView(current, current_map, "norm"),
        nospace=NormalizedView("".join(no_space_chars), no_space_map, "nospace"),
        detensified=NormalizedView(detensified_text, current_map, "detensified"),
        decoded=[
            NormalizedView(item.text, item.interval_map, item.encoding, item.source_span)
            for item in decoded
        ],
        delta=delta,
    )
