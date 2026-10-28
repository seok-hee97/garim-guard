"""Safe CLI: reports locations and rules, never matched source values."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garim", description="로컬 프롬프트 사전 검사기")
    parser.add_argument("--policy", help="정책 YAML 경로")
    parser.add_argument("--mode", choices=("interactive", "thorough"), default="interactive")
    parser.add_argument(
        "--l1-model-dir",
        help="학습된 L1 ONNX 디렉터리(garim_l1.*.onnx·labels.json·토크나이저). 생략하면 L0 만 돈다",
    )
    parser.add_argument(
        "--l1-model-file",
        default="garim_l1.int8.onnx",
        help="--l1-model-dir 안의 ONNX 파일 이름",
    )
    parser.add_argument(
        "--l2-endpoint",
        help="thorough 모드 L2 판정기의 루프백 OpenAI 호환 /v1 주소 "
        "(llama-server·Ollama). 루프백이 아니면 거부한다",
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
    proxy.add_argument("--port", type=int, default=8080)
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


def _is_scan_target(relative: Path) -> bool:
    *directories, name = relative.parts
    if any(part.startswith(".") for part in directories):
        return False  # .git · .venv 같은 숨은 디렉터리
    if name == ".env" or name.startswith(".env."):
        # ★ 숨은 파일 필터가 `.env` 를 늘 건너뛰었다 — 시크릿이 가장 많은 파일이다.
        return True
    return not name.startswith(".") and Path(name).suffix.lower() in _TEXT_SUFFIXES


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"path does not exist: {path}")
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and _is_scan_target(candidate.relative_to(path))
    )


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
    if args.l1_model_dir is None:
        return None
    # `[l1]` 설치는 torch 없이 토크나이저만 쓴다. transformers 가 임포트마다 "PyTorch was not
    # found" 안내를 찍어 판정 출력 앞에 섞인다 — CLI 에서만 끄고, 사용자가 정한 값은 존중한다.
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    from garim.detect.l1 import OnnxL1Backend

    return OnnxL1Backend(args.l1_model_dir, model_filename=args.l1_model_file)


def _l2_backend(args: argparse.Namespace) -> L2Backend | None:
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
            layers = "+".join(
                name
                for name, present in (
                    ("L0", True),
                    ("L1", l1_backend is not None),
                    ("L2", l2_backend is not None),
                )
                if present
            )
            print(
                f"garim proxy: http://{args.host}:{args.port}/v1 -> {args.upstream} "
                f"(mode={args.mode}, layers={layers})",
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
        skipped: list[str] = []
        root = Path(args.path).resolve()
        for path in _iter_files(Path(args.path)):
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
        if skipped:
            # ★ 일부만 읽은 경우는 0 이 나왔다. CP949 한글 파일 하나가 검사되지 않아도
            #   나머지가 깨끗하면 CI 게이트가 통과했다 — 검사가 불완전하면 degraded(3)다.
            return max(highest, 3)
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
