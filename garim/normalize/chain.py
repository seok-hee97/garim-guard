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

# ★ §2.3.1 ③ 이 *"UTS #39 confusables, **버전을 락파일에 고정**. ①과 같은 이유로
#   버전이 바뀌면 골든 테스트가 깨진다"* 고 요구했고, §8.6.1 미결 12번이 *"Python
#   3.11.x 와 unidata 14.0.0 은 고정했는데 **이것만 공백**"* 이라고 적어둔 칸이다.
#
#   여기서 닫는다. 아래 표는 **UTS #39 confusables(Unicode 14.0.0) 의 손으로 고른
#   부분집합**이다 — 전체 파일이 아니다. 전체는 수천 항목이고 한국어 업무문서에
#   나타나지 않는 문자까지 바꿔 오탐을 만든다. 실제로 쓰는 범위는 키릴·그리스 →
#   라틴이라 그 축만 담았다.
#
#   "버전 고정" 을 두 겹으로 강제한다.
#   (1) 이 상수 — 표가 어느 Unicode 판에 대응하는지 이름으로 남긴다.
#   (2) `tests/test_confusables_pin.py` — 런타임 `unidata_version` 이 아래 검증 목록 밖이면
#       실패한다. ① NFKC 와 ③ 이 서로를 무효화하지 않는지 판마다 확인했기 때문이다.
#
#   ★ 처음에는 Python 3.11(Unicode 14.0)만 허용했다. 3.12 ~ 3.14 를 열면서 판마다 NFKC 를
#     전 코드 포인트에서 비교했다(2026-10-28): 14.0 에 있던 문자의 매핑은 **하나도** 바뀌지
#     않았고, 차이는 새로 추가된 문자뿐이다 — 15.0 · 15.1 은 키릴 수식 문자 62자, 16.0 은
#     거기에 외곽선 라틴 문자 36자. 1:N 확장 문자 1,237자(§3.2.4)도 판마다 같다. 이 표의
#     항목 중 새 판의 NFKC 가 먼저 접는 것은 없다(같은 테스트 파일이 항목마다 확인한다).
#
#   ⚠️ 새 판을 목록에 넣을 때는 이 비교를 다시 하고 `requires-python` 상한도 같이 올린다.
CONFUSABLES_UNICODE_VERSION = "14.0.0"
VERIFIED_UNICODE_VERSIONS = frozenset({"14.0.0", "15.0.0", "15.1.0", "16.0.0"})

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

# ★ 5차 검토 — 아래 `_drop_controls` 의 범위 밖에 있는 보이지 않는 문자 하나(U+00AD 소프트
#   하이픈, U+034F, U+180E, 변이 선택자, 한글 채움 문자 …)가 주민번호 · 키 사이에 끼면 L0 가
#   통째로 비껴갔다.
#   지우되 `zero_width` 델타에는 **세지 않는다**: 델타는 L1 입력 feature 다(§3.2.5). 학습 때 없던
#   카운트를 늘리면 이모지의 변이 선택자(U+FE0F)만으로 공격 점수가 움직인다.
#   ★ 전수 검토(2026-10-28) — 몽골어 자유 변이 선택자(U+180B~180D · U+180F, 범주 Mn)와 C0 · C1
#     제어 문자(`\x00` · `\x1b` · `\x9b` …)도 같은 구멍이었다. 제어 문자는 아래에서 지운다.
_INVISIBLE_EXTRA = frozenset(
    {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x2800, 0x3164, 0xFFA0}
)
# 대시류 → `-`. 전화 · 계좌 · 주민번호의 구분자로 쓰이면 규칙이 못 읽었다. 동형 문자 델타에도
# 세지 않는다 — 문장 부호 `—` 는 평범한 한국어에 흔하고, 세면 위와 같은 이유로 L1 이 흔들린다.
_DASHES = {
    chr(code): "-" for code in (0x2010, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x2E3A, 0x2E3B)
}
# 한글 모음 `ㅡ` 를 숫자 사이 구분자로 쓰는 표기(`010ㅡ1234ㅡ5678`). 숫자 사이에서만 바꾼다 —
# 다른 자리의 `ㅡ` 는 자모 결합 단계가 읽어야 한다.
_DIGIT_DASH = re.compile(r"(?<=\d)\u3161(?=\d)")
# JSON 문자열 이스케이프 — `\uXXXX`(서로게이트 쌍 포함)와 `\n` · `\t` · `\"` 등. 겹친 이스케이프
# (`\\uXXXX`, JSON 안의 JSON)도 역슬래시 개수와 상관없이 푼다.
_JSON_ESCAPE = re.compile(
    r"\\+u(?P<high>[dD][89abAB][0-9a-fA-F]{2})\\+u(?P<low>[dD][c-fC-F][0-9a-fA-F]{2})"
    r"|\\+u(?P<code>[0-9a-fA-F]{4})"
    r"|\\+(?P<simple>[ntr\"/])"
)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "/": "/"}


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


def _unescape_json(text: str, source_map: IntervalMap) -> tuple[str, IntervalMap]:
    """JSON 이스케이프를 푼다. 푼 글자는 이스케이프 전체의 원문 구간을 가리킨다.

    ★ 전수 검토(2026-10-28) — 기본 `json.dumps` 는 한글을 `\\uXXXX` 로 낸다. 도구 결과 · 이중 인코딩
      JSON · 문자열 리터럴에 실린 한글(`[\\ub300\\uc678\\ube44]`)을 규칙도 L1 도 읽지 못했다(strict
      에서도 allow). 여기서 풀면 모든 스트림이 읽고, 마스킹은 원문의 이스케이프 구간을 통째로
      바꾸므로 JSON 이 깨지지 않는다. 델타(L1 feature)에는 세지 않는다.
    """
    if "\\" not in text:
        return text, source_map
    output: list[str] = []
    mapped: IntervalMap = []
    index = 0
    for match in _JSON_ESCAPE.finditer(text):
        start, end = match.span()
        if match.group("high"):
            high, low = int(match.group("high"), 16), int(match.group("low"), 16)
            character = chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
        elif match.group("code"):
            code = int(match.group("code"), 16)
            if 0xD800 <= code <= 0xDFFF:
                continue  # 짝 없는 서로게이트 — 그대로 둔다
            character = chr(code)
        else:
            character = _SIMPLE_ESCAPES[match.group("simple")]
        output.append(text[index:start])
        mapped.extend(source_map[index:start])
        output.append(character)
        covered = source_map[start:end]
        mapped.append((min(item[0] for item in covered), max(item[1] for item in covered)))
        index = end
    output.append(text[index:])
    mapped.extend(source_map[index:])
    return "".join(output), mapped


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
        if (
            0x200B <= code <= 0x200F
            or 0x202A <= code <= 0x202E
            or 0x2060 <= code <= 0x206F
            or code == 0xFEFF
        ):
            zero_width += 1
            continue
        category = unicodedata.category(character)
        if (
            code in _INVISIBLE_EXTRA
            or 0xFE00 <= code <= 0xFE0F
            or 0xE0100 <= code <= 0xE01EF
            or category == "Cf"
            or (category == "Cc" and not character.isspace())
        ):
            continue  # 지우되 세지 않는다(위 `_INVISIBLE_EXTRA` 주석)
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


def _tag_views(text: str) -> list[NormalizedView]:
    """유니코드 태그 문자(U+E0020~E007E) 묶음을 ASCII 로 되돌린 뷰.

    ★ 전수 검토(2026-10-28) — 태그 문자는 화면에 보이지 않고 모델은 읽는다("ASCII smuggling").
      정규화가 지우기만 해서, 그 안에 쓴 지시 · 전화번호 · 키를 아무 규칙도 보지 못했다.
    """
    views: list[NormalizedView] = []
    start: int | None = None
    for index in range(len(text) + 1):
        inside = index < len(text) and 0xE0000 <= ord(text[index]) <= 0xE007F
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            decoded = "".join(
                chr(ord(character) - 0xE0000)
                for character in text[start:index]
                if 0xE0020 <= ord(character) <= 0xE007E
            )
            if decoded.strip():
                span = (start, index)
                views.append(NormalizedView(decoded, [span] * len(decoded), "tags", span))
            start = None
    return views


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
    current, current_map = _unescape_json(text, raw_map)
    current, current_map, delta["nfkc"] = _nfkc_charwise(current, current_map)
    current, current_map, delta["zero_width"], delta["tag_chars"] = _drop_controls(
        current, current_map
    )
    current, current_map, delta["homoglyph"] = _translate(current, current_map, _CONFUSABLE)
    current, current_map, _ = _translate(current, current_map, _DASHES)
    current = _DIGIT_DASH.sub("-", current)  # 1:1 치환이라 위치 표는 그대로다
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

    # delta 에는 넣지 않는다 — `DELTA_KEYS` 는 §3.2.5 가 못 박은 8칸이고
    # `normalization_delta_vector` 가 추가 키를 거부한다. 된소리화는 별도 스트림으로
    # 다룬다(§2.3.4). 참고: **feature 계약은 8칸이 아니라 7칸이다** — `space` 가
    # 빠져 있고 그 이유는 `NORMALIZATION_DELTA_ORDER` 옆에 적혀 있다.
    detensified_text, _ = detensify(current)

    decoded = decoded_views(current, current_map)
    delta["b64_decoded"] = len(decoded)  # L1 feature — 태그 뷰는 세지 않는다(tag_chars 가 센다)
    return NormalizedBundle(
        raw=NormalizedView(text, raw_map, "raw"),
        norm=NormalizedView(current, current_map, "norm"),
        nospace=NormalizedView("".join(no_space_chars), no_space_map, "nospace"),
        detensified=NormalizedView(detensified_text, current_map, "detensified"),
        decoded=[
            NormalizedView(item.text, item.interval_map, item.encoding, item.source_span)
            for item in decoded
        ]
        + _tag_views(text),
        delta=delta,
    )
