"""§5.3.2 표기 변형 축 — L1 이 배울 것을 코퍼스에 넣는다.

§3.2.3 이 L1 의 존재 근거를 한 문장으로 적었다.

> **L1이 "L0가 놓치는 변형"을 학습해야 앙상블이 성립한다.**

그런데 생성기는 식별자 양성 22,500행을 **전부 표준 구분자**로 만들고 있었다. PHONE
양성 2,000건이 하나도 빠짐없이 `010-1234-5678` 꼴이었다. L0 정규식이 이미 100% 잡는
표기만 5만 행 보여주면 L1 이 배울 것이 없고, 그러면 앙상블 전제가 무너진다.

이미 측정된 값의 정체이기도 하다 — **PHONE 재현율 합성 1.000 → T5 0.414**(§8.1-16).
T5 에는 자연어 표기가 있고 합성 코퍼스에는 없었다.

## §5.3.2 가 정한 비중

| 표기 | 비중 | 예 | 누가 잡아야 하나 |
|---|---|---|---|
| `standard` | 50% | `900101-1234567` | L0 |
| `spaced` | 15% | `900101 1234567` | L1 |
| `no_separator` | 15% | `9001011234567` | L0(정규식 확장) 또는 L1 |
| `linebreak` | 10% | `900101-\\n1234567` | L1 |
| `spoken` | 10% | `구공공일공일 일이삼사오육칠` | **L1 전담** |

## 왜 이 다섯이 L1 까지 살아서 도착하는가

L1 은 `bundle.norm.text` 를 받는다(`pipeline.py:205`, 학습도 `dataset.py:19` 로 동일).
그래서 **정규화가 되돌리는 은닉은 L1 에 도착하지 않는다** — 자모 분해와 제로위드는
`norm` 단계에서 복원되므로 L1 은 깨끗한 글을 본다.

위 다섯은 하나도 `norm` 이 되돌리지 않는다. 공백 제거는 별도 `nospace` 스트림의 일이고
`norm` 은 숫자를 그대로 둔다. 그래서 이 축은 L1 에 그대로 도달한다 — §5.3.2 는 정합적이다.

## 되돌릴 수 없는 제약 셋 — 비중이 정확히 안 맞는 이유

1. **구분자 없는 값**(`M12345678`·`1234567890`·이메일)에는 적용할 것이 없다.
2. **짝지어진 값**은 손대지 않는다. `checksum_failed_person` 의 짝 음성이 같은 값을
   주문번호 문맥에서 재사용하는데(§5.3.2 쌍 설계), 양성 쪽 표기만 바꾸면 쌍이 깨져
   "본 문자열, 다른 문맥" 대조가 성립하지 않는다.
3. **`linebreak` 는 산문에서만** 쓴다. `structured`(`{"payload": "…"}`)·`code`
   (`payload = "…"`)·`log` 래퍼 안에 생 개행을 넣으면 그 문법 자체가 깨져
   `content_type` 감지가 다른 것을 재게 된다.

그래서 **계획 비중과 실제 비중이 다르다.** `notation_distribution()` 으로 실제 값을
세어 리포트에 적는다 — 계획만 적으면 없는 것을 있다고 말하는 셈이다.

## §5.3.2 에서 구현하지 않은 것 하나

원 표의 네 번째 행은 *"줄바꿈·괄호 삽입"* 인데 **괄호 쪽은 넣지 않았다.** 값 안에
괄호를 넣으면(`900101-(1234567)`) 정답 스팬이 구두점을 포함하게 되고, 어떤 탐지기에도
"괄호까지 스팬으로 내라" 고 요구할 근거가 없다. 실무에서 흔한 괄호 형태는
`900101-1234567 (본인)` 처럼 **값 밖의 주석**이라 표기 변형이 아니다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

STANDARD = "standard"
SPACED = "spaced"
NO_SEPARATOR = "no_separator"
LINEBREAK = "linebreak"
SPOKEN = "spoken"

NOTATIONS = (STANDARD, SPACED, NO_SEPARATOR, LINEBREAK, SPOKEN)

# §5.3.2 의 비중. 100 슬롯을 채우므로 표본 추출이 아니라 **정확한 비중**이다.
NOTATION_WEIGHTS: tuple[tuple[str, int], ...] = (
    (STANDARD, 50),
    (SPACED, 15),
    (NO_SEPARATOR, 15),
    (LINEBREAK, 10),
    (SPOKEN, 10),
)

# 가중치를 그대로 늘어놓으면 앞 50행이 전부 `standard` 라 표기가 템플릿 순서와 붙어버린다.
# 100 과 서로소인 37 로 자리를 옮겨 흩는다 — 난수가 아니므로 재현된다.
_BLOCK = tuple(name for name, weight in NOTATION_WEIGHTS for _ in range(weight))
_SCHEDULE = tuple(_BLOCK[(index * 37) % 100] for index in range(100))

_DIGIT_WORDS = {
    "0": "공",
    "1": "일",
    "2": "이",
    "3": "삼",
    "4": "사",
    "5": "오",
    "6": "육",
    "7": "칠",
    "8": "팔",
    "9": "구",
}
_SEPARATORS = re.compile(r"[- ]")
# 개행을 넣어도 문법이 깨지지 않는 문서 모양. 나머지는 `standard` 로 되돌린다.
# `mixed` 는 본문이 ```text 펜스 안에 들어가므로 생 개행이 문법을 깨지 않는다.
_LINEBREAK_SHAPES = frozenset({"prose", "unknown", "mixed"})


def planned_notation(index: int) -> str:
    """계획 표기. 실제로 적용되는지는 `apply_notation` 이 정한다."""
    return _SCHEDULE[index % len(_SCHEDULE)]


def _spoken(value: str) -> str:
    """숫자를 소리 나는 대로. 구분자 자리는 띄어쓰기로 남긴다."""
    return "".join(" " if _SEPARATORS.fullmatch(char) else _DIGIT_WORDS[char] for char in value)


def apply_notation(value: str, notation: str, *, doc_shape: str) -> tuple[str, str]:
    """(변형된 값, **실제로 적용된** 표기) 를 돌려준다.

    적용할 수 없으면 조용히 `standard` 로 되돌리고 그 사실을 반환값으로 알린다.
    호출자가 계획 표기를 그대로 `meta` 에 적으면 코퍼스가 거짓을 말하게 된다.
    """
    if notation == STANDARD:
        return value, STANDARD
    if notation == SPOKEN:
        # 글자가 섞인 값(여권 `M12345678`·이메일)은 소리로 읽을 수 없다.
        if not all(char in _DIGIT_WORDS or _SEPARATORS.fullmatch(char) for char in value):
            return value, STANDARD
        return _spoken(value), SPOKEN
    if not _SEPARATORS.search(value):
        return value, STANDARD
    if notation == SPACED:
        # 카드처럼 이미 공백으로 끊는 값은 이 변형이 아무것도 바꾸지 않는다.
        if "-" not in value:
            return value, STANDARD
        return value.replace("-", " "), SPACED
    if notation == NO_SEPARATOR:
        return _SEPARATORS.sub("", value), NO_SEPARATOR
    if notation == LINEBREAK:
        if doc_shape not in _LINEBREAK_SHAPES or "-" not in value:
            return value, STANDARD
        # 첫 구분자 뒤에서만 끊는다. 여러 번 끊으면 사람이 쓴 글이 아니다.
        return value.replace("-", "-\n", 1), LINEBREAK
    raise ValueError(f"unknown notation: {notation}")


def notation_distribution(records: Iterable[Any]) -> dict[str, int]:
    """실제 비중. 되돌림 셋 때문에 계획과 다르므로 세어서 보고한다."""
    counts = dict.fromkeys(NOTATIONS, 0)
    for record in records:
        meta: Mapping[str, Any] = getattr(record, "meta", {}) or {}
        notation = meta.get("notation")
        if notation is not None:
            counts[notation] = counts.get(notation, 0) + 1
    return counts
