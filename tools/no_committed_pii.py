"""Fail when a Git-visible file contains a checksum-valid resident-number candidate.

Matched values are never printed. This guard is deliberately standalone so CI can run it before
installing Garim and so importing a design probe cannot leak its verbose sample output.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ★ 구분자가 `-` · 공백 하나뿐이라 합성기 자신의 `linebreak`(`-\n`) · 대시류 변이를 놓쳤다.
#   JSONL 에서는 줄바꿈이 두 글자 `\n` 으로 저장된다 — 그 이스케이프도 구분자로 본다.
PATTERN = re.compile(
    r"(?<!\d)\d{6}(?:[\s\-\u2010-\u2015\u2212\uff0d.]|\\[nrt]){0,3}[1-4]\d{6}(?!\d)"
)
# 합성기의 `spoken` 표기(`구공공일공일 일이삼사오육칠`) — 숫자로 되돌려 같은 검증을 건다.
_SPOKEN_DIGITS = {"공": "0", "영": "0", "일": "1", "이": "2", "삼": "3", "사": "4",
                  "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9"}
SPOKEN = re.compile(r"[공영일이삼사오육칠팔구]{6}\s{0,3}[일이삼사][공영일이삼사오육칠팔구]{6}")


def resident_candidate_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 13 or digits[6] not in "1234":
        return False
    century = 1900 if digits[6] in "12" else 2000
    try:
        born = date(century + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return False
    if born > date.today():  # 미래 생년은 주민번호가 아니다 — garim 의 rrn_date_sex_valid 와 같다
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    total = sum(
        int(character) * weight for character, weight in zip(digits[:12], weights, strict=True)
    )
    # ★ mod 11 필수. 초판이 `(11 - total) % 10` 이라 이 가드가 위반 15건을 놓치면서
    #   CI를 녹색으로 유지했다. 이 파일은 의도적으로 standalone(위 docstring)이라
    #   산식이 garim 과 중복되는데, tests/test_checksums.py 의 교차 검증이
    #   두 사본의 불일치를 잡는다 — 중복은 허용하되 발산은 막는다.
    return (11 - total % 11) % 10 == int(digits[-1])


def resident_candidates(text: str) -> list[int]:
    """체크섬 · 생년월일이 맞는 주민등록번호 후보의 시작 위치. 값은 돌려주지 않는다."""
    starts = [m.start() for m in PATTERN.finditer(text) if resident_candidate_valid(m.group(0))]
    for match in SPOKEN.finditer(text):
        digits = "".join(_SPOKEN_DIGITS.get(character, "") for character in match.group(0))
        if resident_candidate_valid(digits):
            starts.append(match.start())
    return sorted(starts)


def visible_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [root / raw.decode() for raw in output.split(b"\0") if raw]


def scan(root: Path) -> list[tuple[Path, int]]:
    hits: list[tuple[Path, int]] = []
    for path in visible_files(root):
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for start in resident_candidates(body):
            hits.append((path.relative_to(root), body.count("\n", 0, start) + 1))
    return hits


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    hits = scan(root)
    if hits:
        print("checksum-valid resident-number candidate found; values are redacted")
        for path, line in hits[:20]:
            print(f"{path}:{line}")
        return 1
    print(f"OK: no checksum-valid resident-number candidate in {len(visible_files(root))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
