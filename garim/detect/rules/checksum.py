"""Deterministic validation helpers for identifiers with published algorithms."""

from __future__ import annotations

import re
from datetime import date


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def rrn_date_sex_valid(value: str) -> bool:
    number = digits(value)
    if len(number) != 13 or number[6] not in "12345678":
        return False
    century = 1900 if number[6] in "1256" else 2000
    try:
        date(century + int(number[:2]), int(number[2:4]), int(number[4:6]))
    except ValueError:
        return False
    return True


def rrn_checksum_valid(value: str) -> bool:
    number = digits(value)
    if len(number) != 13 or not rrn_date_sex_valid(number):
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    weighted = sum(int(digit) * weight for digit, weight in zip(number[:12], weights, strict=True))
    # ★ mod 11 을 빠뜨리면 진짜 주민번호의 89.5%를 통과시킨다.
    #   초판이 `(11 - weighted) % 10` 이었다 — docs/probes/checksum_probe.py:17 과 어긋났고,
    #   그 결과 유효/무효 판정이 문자 그대로 뒤집혔다(무효를 block, 유효를 allow).
    #   tests/test_checksums.py 의 양성 단언이 이 회귀를 잡는다.
    #   ⚠️ 이 주석에 실제 번호 예시를 쓰지 마라 — tools/no_committed_pii.py 가 잡는다.
    expected = (11 - weighted % 11) % 10
    return expected == int(number[-1])


def business_checksum_valid(value: str) -> bool:
    number = digits(value)
    if len(number) != 10:
        return False
    weights = (1, 3, 7, 1, 3, 7, 1, 3, 5)
    total = sum(int(digit) * weight for digit, weight in zip(number[:9], weights, strict=True))
    total += (int(number[8]) * 5) // 10
    return (10 - total % 10) % 10 == int(number[-1])


def corporation_checksum_valid(value: str) -> bool:
    number = digits(value)
    if len(number) != 13:
        return False
    total = sum(
        int(digit) * (1 if index % 2 == 0 else 2) for index, digit in enumerate(number[:12])
    )
    return (10 - total % 10) % 10 == int(number[-1])


def luhn_valid(value: str) -> bool:
    number = digits(value)
    if not 13 <= len(number) <= 19 or len(set(number)) == 1:
        return False
    total = 0
    parity = len(number) % 2
    for index, character in enumerate(number):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
