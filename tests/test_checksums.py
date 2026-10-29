from __future__ import annotations

from garim.detect.rules.checksum import (
    business_checksum_valid,
    corporation_checksum_valid,
    luhn_valid,
    rrn_checksum_valid,
    rrn_date_sex_valid,
)


def _business_number(base: str) -> str:
    weights = (1, 3, 7, 1, 3, 7, 1, 3, 5)
    total = sum(int(digit) * weight for digit, weight in zip(base, weights, strict=False))
    total += (int(base[8]) * 5) // 10
    return base + str((10 - total % 10) % 10)


def _corporation_number(base: str) -> str:
    total = sum(int(digit) * (1 if index % 2 == 0 else 2) for index, digit in enumerate(base))
    return base + str((10 - total % 10) % 10)


def _luhn_number(base: str) -> str:
    for check in range(10):
        candidate = base + str(check)
        if luhn_valid(candidate):
            return candidate
    raise AssertionError("no Luhn digit")


def test_published_checksum_algorithms_accept_constructed_non_personal_identifiers() -> None:
    business = _business_number("123456789")
    corporation = _corporation_number("123456789012")
    card = _luhn_number("400000000000000")
    assert business_checksum_valid(business)
    assert corporation_checksum_valid(corporation)
    assert luhn_valid(card)


def test_rrn_requires_date_sex_and_checksum() -> None:
    candidate = "200101-3000000"
    assert rrn_date_sex_valid(candidate)
    assert not rrn_checksum_valid(candidate)
    assert not rrn_date_sex_valid("201332-3000000")


def _rrn_check_digit(front12: str) -> int:
    """공표 알고리즘 — docs/probes/checksum_probe.py:12-18 의 참조 구현과 동일.

    이 테스트가 값을 **런타임에 생성**하는 이유: 체크섬 유효 주민번호는 실존 번호와
    우연히 일치할 수 있어(2세대 7.0e-3) DESIGN §5.3.5.1 이 저장소 커밋을 금지했다.
    상수로 박으면 tools/no_committed_pii.py 가 이 파일을 위반으로 잡는다.
    """
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    total = sum(int(d) * w for d, w in zip(front12, weights, strict=True))
    return (11 - total % 11) % 10


def test_rrn_checksum_accepts_values_built_by_the_published_algorithm() -> None:
    """★ 회귀 방지 — 초판은 mod 11 을 빠뜨려 진짜 주민번호의 89.5%를 거부했다.

    음성 단언만으로는 이 버그가 잡히지 않는다. `200101-3000000` 은 옳은 산식으로도
    틀린 산식으로도 무효라 두 구현을 구별하지 못했다. 양성 단언이 필요하다.
    """
    fronts = ["900101" + "1" + "23456", "801231" + "2" + "00000", "050229" + "3" + "11111"]
    accepted = 0
    for front12 in fronts:
        if not rrn_date_sex_valid(front12 + "0"):
            continue  # 존재하지 않는 날짜(2005-02-29 등)는 건너뛴다
        value = front12 + str(_rrn_check_digit(front12))
        assert rrn_checksum_valid(value), "공표 알고리즘으로 만든 값을 거부했다: mod 11 누락?"
        # 마지막 자리를 바꾸면 반드시 실패해야 한다
        wrong = front12 + str((int(value[-1]) + 1) % 10)
        assert not rrn_checksum_valid(wrong)
        accepted += 1
    assert accepted >= 2


def test_guard_tool_checksum_agrees_with_garim() -> None:
    """tools/no_committed_pii.py 는 의도적으로 standalone 이라 산식이 중복된다.

    중복 자체는 허용하되 **발산은 막는다** — 두 사본이 갈라진 것이 이번 결함의
    근인이었고, 틀린 가드가 CI를 녹색으로 유지하며 위반 15건을 숨겼다.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from no_committed_pii import resident_candidate_valid

    disagreements = []
    for front11 in ("90010112345", "80123120000", "75061013579", "00010330000"):
        for last in range(10):
            front12 = front11 + str(last)
            for check in range(10):
                value = front12 + str(check)
                if not rrn_date_sex_valid(value) or value[6] not in "1234":
                    continue
                if rrn_checksum_valid(value) != resident_candidate_valid(value):
                    disagreements.append(value)
    assert not disagreements, f"가드와 garim 의 체크섬이 {len(disagreements)}건 어긋난다"


def test_guard_tool_scans_commit_messages(tmp_path) -> None:  # noqa: ANN001
    """★ 파일만 봐서 커밋 메시지의 합성 주민번호가 공개 이력에 남았다."""
    import subprocess
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from no_committed_pii import scan_messages

    front = "900101123456"
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    check = (11 - sum(int(d) * w for d, w in zip(front, weights, strict=True)) % 11) % 10
    value = f"{front[:6]}-{front[6:]}{check}"
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "clean"], check=True)
    assert scan_messages(tmp_path) == []
    subprocess.run(
        [*git, "commit", "-q", "--allow-empty", "-m", f"fix: {value} 을 막는다"], check=True
    )
    assert len(scan_messages(tmp_path)) == 1
