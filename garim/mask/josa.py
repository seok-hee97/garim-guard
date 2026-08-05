"""Correct Korean particle allomorphs immediately following a restored vault token."""

from __future__ import annotations

from garim.normalize.hangul import has_final_consonant


def _jongseong_index(text: str) -> int:
    if not text:
        return 0
    code = ord(text[-1])
    return (code - 0xAC00) % 28 if 0xAC00 <= code <= 0xD7A3 else 0


def correct_particle(value: str, particle: str) -> str:
    if not value or not 0xAC00 <= ord(value[-1]) <= 0xD7A3:
        return particle
    has_final = has_final_consonant(value)
    if particle in {"이", "가"}:
        return "이" if has_final else "가"
    if particle in {"을", "를"}:
        return "을" if has_final else "를"
    if particle in {"은", "는"}:
        return "은" if has_final else "는"
    if particle in {"과", "와"}:
        return "과" if has_final else "와"
    if particle in {"으로", "로"}:
        return "으로" if has_final and _jongseong_index(value) != 8 else "로"
    return particle
