"""통계 — 대응 표본 McNemar exact + Wilson score CI.

모든 arm 이 동일 케이스 집합을 돌므로 층별 한계 기여는 대응 표본이다. 독립 표본
가정 시 MDD≈12pp 로 Tier B 기여가 안 보인다. McNemar 로 4~5pp 로 내려간다.
"""

from __future__ import annotations

from typing import Sequence


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """이항 비율의 Wilson score 95% CI."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d
    return (max(0.0, center - half), min(1.0, center + half))


def mcnemar_exact(a_flags: Sequence[bool], b_flags: Sequence[bool]) -> dict:
    """대응 이진 결과 두 arm 의 McNemar exact test.

    a_flags/b_flags: 같은 케이스 순서의 성공(True)/실패(False). 예: executed 여부.
    불일치 쌍(b01=a성공·b실패, b10=a실패·b성공)만 사용.
    """
    assert len(a_flags) == len(b_flags), "대응 표본이어야 한다 (같은 케이스 집합)"
    b01 = sum(1 for a, b in zip(a_flags, b_flags) if a and not b)
    b10 = sum(1 for a, b in zip(a_flags, b_flags) if not a and b)
    n_disc = b01 + b10
    # 정확 이항검정: p = 2 * sum_{k<=min} C(n,k) 0.5^n  (양측)
    from math import comb
    if n_disc == 0:
        p = 1.0
    else:
        k = min(b01, b10)
        tail = sum(comb(n_disc, i) for i in range(0, k + 1)) * (0.5 ** n_disc)
        p = min(1.0, 2 * tail)
    return {"b01": b01, "b10": b10, "n_discordant": n_disc, "p_value": round(p, 5)}
