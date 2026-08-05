"""Safe CLI: reports locations and rules, never matched source values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from garim.api import Inspector
from garim.models import CheckResult

_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".env",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".log",
    ".md",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garim", description="로컬 프롬프트 사전 검사기")
    parser.add_argument("--policy", help="정책 YAML 경로")
    parser.add_argument("--mode", choices=("interactive", "thorough"), default="interactive")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="문자열 또는 표준입력 검사")
    check.add_argument("text", nargs="?", help="생략하면 표준입력 사용")
    check.add_argument("--json", action="store_true", dest="as_json")

    scan = subparsers.add_parser("scan", help="파일 또는 디렉터리 검사")
    scan.add_argument("path")
    scan.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _exit_code(result: CheckResult) -> int:
    if result.degraded:
        return 3
    return {"allow": 0, "mask": 0, "warn": 1, "block": 2}[result.verdict]


def _print_result(result: CheckResult, *, as_json: bool, prefix: str | None = None) -> None:
    if as_json:
        payload = result.public_dict()
        if prefix is not None:
            payload["path"] = prefix
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    heading = f"{prefix}: " if prefix else ""
    print(f"{heading}{result.verdict} (degraded={str(result.degraded).lower()})")
    for finding in result.leaks + result.attacks:
        print(
            f"  {finding.code} segment={finding.segment_idx} span={finding.span} "
            f"confidence={finding.confidence:.2f} rule={finding.rule}"
        )
    if result.masked_text is not None:
        print(result.masked_text)


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"path does not exist: {path}")
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and not any(part.startswith(".") for part in candidate.relative_to(path).parts)
        and candidate.suffix.lower() in _TEXT_SUFFIXES
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        inspector = Inspector(args.policy)
        if args.command == "check":
            text = args.text if args.text is not None else sys.stdin.read()
            result = inspector.check(text, mode=args.mode)
            _print_result(result, as_json=args.as_json)
            return _exit_code(result)

        highest = 0
        for path in _iter_files(Path(args.path)):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            result = inspector.check(source, mode=args.mode)
            _print_result(result, as_json=args.as_json, prefix=str(path))
            highest = max(highest, _exit_code(result))
        return highest
    except (OSError, TypeError, ValueError) as exc:
        print(f"garim: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
