"""Safe CLI: reports locations and rules, never matched source values."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from garim._version import __version__
from garim.api import Inspector
from garim.detect.l1 import L1Backend
from garim.detect.l2 import L2Backend
from garim.models import CheckResult
from garim.policy.engine import SEVERITY

_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".key",
    ".kt",
    ".log",
    ".md",
    ".pem",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
# 이보다 큰 파일은 읽지 않고 "검사 안 됨" 으로 알린다 — 통째로 메모리에 올리지 않는다.
_MAX_SCAN_BYTES = 64 * 1024 * 1024
# 큰 파일을 나눌 때 창끼리 겹치는 글자 수 — 경계에 걸친 식별자 · 키가 한 창에는 온전히 든다.
_WINDOW_OVERLAP = 2_000


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # ★ argparse 는 사용법 오류에 2 를 내는데 여기서 2 는 block 이다 — 실행 오류(4)로 낸다.
        self.print_usage(sys.stderr)
        self.exit(4, f"{self.prog}: error: {message}\n")


def _port(value: str) -> int:
    # ★ 범위 밖 포트는 bind() 의 OverflowError 트레이스백 · 종료 코드 1(warn)로 끝났다.
    if not value.isdigit() or int(value) > 65535:
        raise argparse.ArgumentTypeError(f"port must be 0-65535: {value!r}")
    return int(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="garim", description="로컬 프롬프트 사전 검사기")
    parser.add_argument("--version", action="version", version=f"garim-guard {__version__}")
    parser.add_argument("--policy", help="정책 YAML 경로")
    parser.add_argument("--mode", choices=("interactive", "thorough"), default="interactive")
    l1_source = parser.add_mutually_exclusive_group()
    l1_source.add_argument(
        "--l1-model-dir",
        help="학습된 L1 ONNX 디렉터리(garim_l1.*.onnx·labels.json·토크나이저). 생략하면 L0 만 돈다",
    )
    l1_source.add_argument(
        "--l1-backend",
        metavar="MODULE:NAME",
        help="패키지 밖 L1 모델 — L1Backend 를 돌려주는 `모듈:함수` 또는 `파일.py:함수`"
        "(docs/INTEGRATION.md)",
    )
    parser.add_argument(
        "--l1-model-file",
        default="garim_l1.int8.onnx",
        help="--l1-model-dir 안의 ONNX 파일 이름",
    )
    l2_source = parser.add_mutually_exclusive_group()
    l2_source.add_argument(
        "--l2-endpoint",
        help="thorough 모드 L2 판정기의 루프백 OpenAI 호환 /v1 주소 "
        "(llama-server·Ollama). 루프백이 아니면 거부한다",
    )
    l2_source.add_argument(
        "--l2-backend",
        metavar="MODULE:NAME",
        help="패키지 밖 L2 판정기 — L2Backend 를 돌려주는 `모듈:함수` 또는 `파일.py:함수`",
    )
    parser.add_argument(
        "--l2-model",
        default="gemma-4-e2b-it",
        help="L2 서버에 넘길 모델 이름. 기본은 Gemma 4 E2B(§6.4)",
    )
    parser.add_argument("--l2-timeout", type=float, default=30.0, help="L2 호출당 제한(초)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="문자열 또는 표준입력 검사")
    check.add_argument("text", nargs="?", help="생략하면 표준입력 사용")
    check.add_argument("--json", action="store_true", dest="as_json")

    scan = subparsers.add_parser("scan", help="파일 또는 디렉터리 검사")
    scan.add_argument("path")
    scan.add_argument("--json", action="store_true", dest="as_json")
    scan.add_argument(
        "--show-masked",
        action="store_true",
        help="파일의 마스킹본을 출력한다. 기본은 탐지 목록만 — warn 탐지와 놓친 값은 원문 그대로라 "
        "CI 로그에 남는다",
    )

    # §1.1 세 번째 사용 방식 — 앱 코드를 고치지 않고 `base_url` 만 바꿔 끼운다.
    proxy = subparsers.add_parser("proxy", help="OpenAI 호환 로컬 프록시 실행")
    proxy.add_argument(
        "--upstream",
        required=True,
        help="상류 base URL. 코드에 박힌 기본값을 두지 않는다 — 목적지는 항상 선언이다.",
    )
    proxy.add_argument("--host", default="127.0.0.1", help="기본값은 루프백이다")
    proxy.add_argument(
        "--upstream-timeout",
        type=float,
        default=60.0,
        help="상류 응답 대기(초). 사고(thinking) 모델은 60초를 넘기기도 한다",
    )
    proxy.add_argument("--port", type=_port, default=8080)
    proxy.add_argument(
        "--refuse-streaming",
        action="store_const",
        const="refuse",
        default="buffered",
        dest="streaming",
        help="stream=true 요청을 거부한다. 기본은 모아서 복원한 뒤 SSE 로 돌려준다",
    )
    proxy.add_argument(
        "--no-forward-authorization",
        action="store_false",
        dest="forward_authorization",
        help="클라이언트의 Authorization 헤더를 상류로 넘기지 않는다",
    )
    return parser


def _exit_code(result: CheckResult) -> int:
    # ★ 전수 검토 — degraded(3)가 block(2)을 가려서, 2 에만 실패하는 게이트가 주민번호가 든
    #   10만 자 입력 · 깨진 인코딩 파일이 섞인 디렉터리를 통과시켰다. block 이 먼저다.
    if result.verdict == "block":
        return 2
    if result.degraded:
        return 3
    return {"allow": 0, "mask": 0, "warn": 1}[result.verdict]


# 여러 결과를 합칠 때의 무게 — block(2) > degraded(3) > warn(1) > allow · mask(0).
_EXIT_WEIGHT = {0: 0, 1: 1, 3: 2, 2: 3}


def _heavier(left: int, right: int) -> int:
    return left if _EXIT_WEIGHT[left] >= _EXIT_WEIGHT[right] else right


def _print_result(
    result: CheckResult, *, as_json: bool, prefix: str | None = None, show_masked: bool = True
) -> None:
    if as_json:
        payload = result.public_dict()
        if prefix is not None:
            payload["path"] = prefix
        if not show_masked:
            payload.pop("masked_text", None)
            payload.pop("masked_segments", None)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    heading = f"{prefix}: " if prefix else ""
    print(f"{heading}{result.verdict} (degraded={str(result.degraded).lower()})")
    for finding in result.leaks + result.attacks:
        print(
            f"  {finding.code} segment={finding.segment_idx} span={finding.span} "
            f"confidence={finding.confidence:.2f} rule={finding.rule}"
        )
    if show_masked and result.masked_text is not None:
        print(result.masked_text)


def _is_scan_target(relative: Path) -> bool:
    *directories, name = relative.parts
    if any(part.startswith(".") for part in directories):
        return False  # .git · .venv 같은 숨은 디렉터리
    if name == ".env" or name.startswith(".env."):
        # ★ 숨은 파일 필터가 `.env` 를 늘 건너뛰었다 — 시크릿이 가장 많은 파일이다.
        return True
    return not name.startswith(".") and Path(name).suffix.lower() in _TEXT_SUFFIXES


def _iter_files(path: Path) -> tuple[list[Path], list[str], int]:
    """검사할 파일, 읽지 못한 디렉터리, 형식 필터로 뺀 파일 수.

    ★ 전수 검토 — `rglob` 은 열 수 없는 하위 디렉터리를 조용히 건너뛰어, 권한 없는 디렉터리의
      주민번호 파일을 두고도 0 을 냈다. 읽지 못한 디렉터리는 검사하지 못한 것으로 알린다.
    """
    if path.is_file():
        return [path], [], 0
    if not path.is_dir():
        raise ValueError(f"path does not exist: {path}")
    files: list[Path] = []
    errors: list[str] = []
    excluded = 0

    def unreadable(error: OSError) -> None:
        errors.append(f"{error.filename}: {type(error).__name__}")

    for directory, subdirectories, names in os.walk(path, onerror=unreadable):
        subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
        for name in names:
            candidate = Path(directory) / name
            if not candidate.is_file():
                continue
            if _is_scan_target(candidate.relative_to(path)):
                files.append(candidate)
            else:
                excluded += 1
    return sorted(files), errors, excluded


def _windows(text: str, limit: int) -> list[tuple[int, str]]:
    """`limit` 글자 이하의 겹치는 창으로 나눈다. 가능하면 줄 경계에서 자른다.

    겹침은 창의 1/4 을 넘지 않는다 — ★ 6차 검토: 상한을 작게 둔 정책에서 겹침이 창만 해져
    한 글자씩 전진했다(3천 자 파일에 522회).
    """
    overlap = min(_WINDOW_OVERLAP, limit // 4)
    windows: list[tuple[int, str]] = []
    start = 0
    while True:
        end = min(len(text), start + limit)
        if end < len(text):
            cut = text.rfind("\n", start + limit // 2, end)
            if cut > start:
                end = cut + 1
        windows.append((start, text[start:end]))
        if end >= len(text):
            return windows
        start = max(start + 1, end - overlap)


def _check_file(inspector: Inspector, source: str, mode: str) -> CheckResult:
    """파일 하나를 검사한다. 입력 상한보다 길면 창으로 나눠 **끝까지** 본다.

    ★ 5차 검토 — 상한(기본 10만 자)을 넘는 파일은 잘린 입력이라 block · degraded 만 내고
      탐지 목록은 비어 있었다. 10만 500번째 글자의 AWS 키는 끝내 보고되지 않았다. 스캔은
      외부로 보내는 경로가 아니라 보고서이므로, 잘라서 막는 대신 나눠서 다 본다.
    """
    limit = inspector.policy.config.limits.max_input_chars
    if len(source) <= limit:
        return inspector.check(source, mode=mode)
    results = []
    seen: set[tuple[str, tuple[int, int]]] = set()
    leaks, attacks = [], []
    for offset, window in _windows(source, limit):
        result = inspector.check(window, mode=mode)
        results.append(result)
        for bucket, findings in ((leaks, result.leaks), (attacks, result.attacks)):
            for finding in findings:
                span = (finding.span[0] + offset, finding.span[1] + offset)
                if (finding.code, span) not in seen:
                    seen.add((finding.code, span))
                    bucket.append(replace(finding, span=span))
    first = results[0]
    return replace(
        first,
        verdict=max((result.verdict for result in results), key=SEVERITY.__getitem__),
        leaks=leaks,
        attacks=attacks,
        masked_text=None,  # 겹치는 창의 마스킹본은 이어 붙일 수 없다 — 탐지 위치만 보고한다
        masked_segments=None,
        degraded=any(result.degraded for result in results),
        degraded_reasons=sorted({r for result in results for r in result.degraded_reasons}),
    )


def _l1_backend(args: argparse.Namespace) -> L1Backend | None:
    if args.l1_backend is not None:
        from garim.backends import load_backend

        return load_backend(args.l1_backend, layer="L1")
    if args.l1_model_dir is None:
        return None
    # `[l1]` 설치는 torch 없이 토크나이저만 쓴다. transformers 가 임포트마다 "PyTorch was not
    # found" 안내를 찍어 판정 출력 앞에 섞인다 — CLI 에서만 끄고, 사용자가 정한 값은 존중한다.
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    from garim.detect.l1 import OnnxL1Backend

    return OnnxL1Backend(args.l1_model_dir, model_filename=args.l1_model_file)


def _l2_backend(args: argparse.Namespace) -> L2Backend | None:
    if args.l2_backend is not None:
        from garim.backends import load_backend

        return load_backend(args.l2_backend, layer="L2")
    if args.l2_endpoint is None:
        return None
    from garim.detect.l2 import LocalChatL2Backend

    return LocalChatL2Backend(
        args.l2_endpoint, args.l2_model, timeout_seconds=args.l2_timeout
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        l1_backend = _l1_backend(args)
        l2_backend = _l2_backend(args)
        inspector = Inspector(args.policy, l1_backend=l1_backend, l2_backend=l2_backend)
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
                timeout_seconds=args.upstream_timeout,
                streaming=args.streaming,
                l1_model_dir=args.l1_model_dir,
                l1_model_file=args.l1_model_file,
            )
            server = build_server(
                config,
                host=args.host,
                port=args.port,
                l1_backend=l1_backend,
                l2_backend=l2_backend,
            )
            # L2 는 thorough 에서만 돈다 — interactive 배너에 L2 를 적으면 거짓이다(전수 검토).
            layers = "+".join(
                name
                for name, present in (
                    ("L0", True),
                    ("L1", l1_backend is not None),
                    ("L2", l2_backend is not None and args.mode == "thorough"),
                )
                if present
            )
            bound_host, bound_port = server.server_address[:2]
            display_host = f"[{bound_host}]" if ":" in str(bound_host) else bound_host
            print(
                f"garim proxy: http://{display_host}:{bound_port}/v1 -> {args.upstream} "
                f"(mode={args.mode}, layers={layers})",
                file=sys.stderr,
            )
            if l2_backend is not None and args.mode != "thorough":
                print(
                    "garim proxy: warning — L2 is configured but runs only with --mode thorough",
                    file=sys.stderr,
                )
            if args.host not in _LOOPBACK_HOSTS:
                # 프록시는 인증 없이 받은 요청을 사용자의 Authorization 헤더째 상류로 보낸다.
                print(
                    f"garim proxy: warning — {args.host} is not loopback; anyone who can reach "
                    "this port can send requests through it",
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
        root = Path(args.path).resolve()
        targets, unreadable, excluded = _iter_files(Path(args.path))
        skipped: list[str] = [f"{entry} (directory)" for entry in unreadable]
        if excluded:
            print(
                f"garim: {excluded} files not inspected — not a text type garim scans "
                "(see README for the list)",
                file=sys.stderr,
            )
        for path in targets:
            seen += 1
            if root.is_dir() and not path.resolve().is_relative_to(root):
                # ★ 파일 심볼릭 링크를 따라 스캔 루트 밖(`../outside/private.conf`)을 읽고
                #   마스킹본을 출력했다(5차 검토). 루트 밖은 검사 대상이 아니다.
                skipped.append(f"{path}: symlink outside scan root")
                continue
            try:
                if path.stat().st_size > _MAX_SCAN_BYTES:
                    skipped.append(f"{path}: larger than {_MAX_SCAN_BYTES // (1024 * 1024)}MB")
                    continue
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                # ★ 조용히 넘기면 안 된다. 읽지 못한 파일은 **검사되지 않은 파일**이고,
                #   CI 게이트가 `garim scan` 종료 코드를 보고 있으면 그대로 fail-open 이
                #   된다. 실제로 UTF-8 이 아닌 파일 둘만 있는 디렉터리에서 아무것도
                #   출력하지 않고 0 을 냈다.
                skipped.append(f"{path}: {type(exc).__name__}")
                continue
            result = _check_file(inspector, source, args.mode)
            _print_result(
                result, as_json=args.as_json, prefix=str(path), show_masked=args.show_masked
            )
            highest = _heavier(highest, _exit_code(result))
        for entry in skipped:
            print(f"garim: skipped (not inspected) {entry}", file=sys.stderr)
        if (seen or unreadable) and seen + len(unreadable) == len(skipped):
            # 파일은 있었는데 한 건도 검사하지 못했다 — 통과라고 말할 근거가 없다.
            print(
                f"garim: inspected 0 of {seen} files; refusing to report success",
                file=sys.stderr,
            )
            return 4
        if skipped:
            # ★ 일부만 읽은 경우는 0 이 나왔다. CP949 한글 파일 하나가 검사되지 않아도
            #   나머지가 깨끗하면 CI 게이트가 통과했다 — 검사가 불완전하면 degraded(3)다.
            return _heavier(highest, 3)
        if not seen:
            print(f"garim: no files to inspect under {args.path}", file=sys.stderr)
        return highest
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        # RuntimeError: L1 extra(onnxruntime·transformers) 미설치. 모델을 달라고 했는데
        # 못 올렸으면 L0 만으로 조용히 도는 대신 시작하지 않는다.
        print(f"garim: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
