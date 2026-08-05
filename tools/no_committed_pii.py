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

PATTERN = re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)")


def resident_candidate_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 13 or digits[6] not in "1234":
        return False
    century = 1900 if digits[6] in "12" else 2000
    try:
        date(century + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
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
        for match in PATTERN.finditer(body):
            if resident_candidate_valid(match.group(0)):
                hits.append((path.relative_to(root), body.count("\n", 0, match.start()) + 1))
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
