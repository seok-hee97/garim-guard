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

    # §1.1 세 번째 사용 방식 — 앱 코드를 고치지 않고 `base_url` 만 바꿔 끼운다.
    proxy = subparsers.add_parser("proxy", help="OpenAI 호환 로컬 프록시 실행")
    proxy.add_argument(
        "--upstream",
        required=True,
        help="상류 base URL. 코드에 박힌 기본값을 두지 않는다 — 목적지는 항상 선언이다.",
    )
    proxy.add_argument("--host", default="127.0.0.1", help="기본값은 루프백이다")
    proxy.add_argument("--port", type=int, default=8080)
    proxy.add_argument(
        "--no-forward-authorization",
        action="store_false",
        dest="forward_authorization",
        help="클라이언트의 Authorization 헤더를 상류로 넘기지 않는다",
    )
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

        if args.command == "proxy":
            from garim.proxy import ProxyConfig, build_server

            config = ProxyConfig(
                upstream_base_url=args.upstream,
                policy_path=args.policy,
                mode=args.mode,
                forward_authorization=args.forward_authorization,
            )
            server = build_server(config, host=args.host, port=args.port)
            print(
                f"garim proxy: http://{args.host}:{args.port}/v1 -> {args.upstream} "
                f"(mode={args.mode})",
                file=sys.stderr,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0

        highest = 0
        seen = 0
        skipped: list[str] = []
        for path in _iter_files(Path(args.path)):
            seen += 1
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                # ★ 조용히 넘기면 안 된다. 읽지 못한 파일은 **검사되지 않은 파일**이고,
                #   CI 게이트가 `garim scan` 종료 코드를 보고 있으면 그대로 fail-open 이
                #   된다. 실제로 UTF-8 이 아닌 파일 둘만 있는 디렉터리에서 아무것도
                #   출력하지 않고 0 을 냈다.
                skipped.append(f"{path}: {type(exc).__name__}")
                continue
            result = inspector.check(source, mode=args.mode)
            _print_result(result, as_json=args.as_json, prefix=str(path))
            highest = max(highest, _exit_code(result))
        for entry in skipped:
            print(f"garim: skipped (not inspected) {entry}", file=sys.stderr)
        if seen and seen == len(skipped):
            # 파일은 있었는데 한 건도 검사하지 못했다 — 통과라고 말할 근거가 없다.
            print(
                f"garim: inspected 0 of {seen} files; refusing to report success",
                file=sys.stderr,
            )
            return 4
        return highest
    except (OSError, TypeError, ValueError) as exc:
        print(f"garim: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
