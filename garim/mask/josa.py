"""Correct Korean particle allomorphs immediately following a restored vault token."""

from __future__ import annotations

from garim.normalize.hangul import has_final_consonant

# 끝자리 숫자를 한자어로 읽었을 때의 받침. 영·일·삼·육·칠·팔은 받침이 있고 그중
# 일·칠·팔은 ㄹ 이다.
#
# ★ 초판은 한글로 끝나는 값만 교정했다. 그런데 L0 가 실제로 가리는 값 — 전화·카드·
#   계좌·주민번호 — 은 전부 숫자로 끝나서, 교정이 쓰일 수 있는 라벨이 L1 전담인
#   PERSON·ADDRESS 둘뿐이었다. 가중치가 없는 지금은 한 번도 발동하지 않았다.
#   `010-1234-5678를` 이 그대로 나갔다(팔을).
#
# 숫자를 자릿수로 읽든(`…영`) 수로 읽든(`…십`·`…백`·`…천`·`…만`) 0 은 ㄹ 이 아닌
# 받침이라 결과가 같다. 그래서 끝자리 하나만 본다. 라틴 문자는 읽는 법이 정해지지
# 않아(`.com` 컴 / `.org` 오알지) 교정하지 않는다.
_DIGIT_FINAL: dict[str, str | None] = {
    "0": "ㅇ",
    "1": "ㄹ",
    "2": None,
    "3": "ㅁ",
    "4": None,
    "5": None,
    "6": "ㄱ",
    "7": "ㄹ",
    "8": "ㄹ",
    "9": None,
}


def _jongseong_index(text: str) -> int:
    if not text:
        return 0
    code = ord(text[-1])
    return (code - 0xAC00) % 28 if 0xAC00 <= code <= 0xD7A3 else 0


def _final_of(value: str) -> tuple[bool, bool] | None:
    """(받침 유무, ㄹ 받침 여부). 읽는 법을 정할 수 없으면 None."""
    last = value[-1]
    if 0xAC00 <= ord(last) <= 0xD7A3:
        return has_final_consonant(value), _jongseong_index(value) == 8
    if last in _DIGIT_FINAL:
        final = _DIGIT_FINAL[last]
        return final is not None, final == "ㄹ"
    return None


def correct_particle(value: str, particle: str) -> str:
    if not value:
        return particle
    final = _final_of(value)
    if final is None:
        return particle
    has_final, rieul = final
    if particle in {"이", "가"}:
        return "이" if has_final else "가"
    if particle in {"을", "를"}:
        return "을" if has_final else "를"
    if particle in {"은", "는"}:
        return "은" if has_final else "는"
    if particle in {"과", "와"}:
        return "과" if has_final else "와"
    if particle in {"으로", "로"}:
        return "으로" if has_final and not rieul else "로"
    return particle
