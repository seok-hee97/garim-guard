"""OpenAI 호환 로컬 프록시 — §1.1 세 번째 사용 방식.

앱 코드를 고치지 않고 `base_url` 만 바꿔 검사를 끼워 넣는다. §8.4-2 가 절삭 목록에서
이것을 뺀 이유는 편의가 아니라 **데이터 구조의 근거**다 — `CheckResult.masked_segments`
가 존재하는 이유가 OpenAI `messages` 배열이고, 프록시를 자르면 그 필드가 왜 있는지
설명할 수 없게 된다(§1.1).

## 못박은 것 넷

1. **차단은 상류로 보내지 않는다.** `verdict == "block"` 이면 요청 자체를 만들지 않고
   400 을 돌려준다. 검사기가 통과시키지 않은 것이 밖으로 나가는 경로를 두지 않는다.
2. **복원은 돌아오는 길에서만 한다.** 마스킹된 프롬프트를 보내고, 응답 본문을 같은
   볼트로 되돌려 앱에 준다(§3.3). 볼트는 요청 하나의 수명이다.
3. **스트리밍은 지원하지 않고 거부한다.** 조각난 응답에서는 토큰이 청크 경계에 걸려
   복원이 깨진다. 조용히 복원 없이 흘려보내는 것보다 거부가 맞다(§3.6 의 fail 방향).
4. **상류 주소는 설정으로만 받는다.** 코드에 박힌 기본 상류가 없다 — 사용자가 명시하지
   않으면 프록시가 뜨지 않는다. §1 의 "검사기 자체는 아무 데도 보내지 않는다" 를
   프록시에서도 깨지 않으려면 목적지가 항상 사용자의 선언이어야 한다.

⚠️ 프록시는 **상류로 보낸다** — 그것이 존재 이유다. §1 의 "외부 통신 0" 은 검사
경로에 대한 주장이고, `tools/no_external_calls.py` 가 그 경계를 검증한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from garim.api import Inspector
from garim.models import Origin, Segment
from garim.tls import https_context

# OpenAI 역할 → §3.2.1 origin. `tool` 과 `function` 은 모델이 아니라 외부가 채운
# 내용이라 간접 인젝션 경로다(§2.5b) — `user` 로 접지 않는다.
ROLE_TO_ORIGIN: dict[str, Origin] = {
    "system": "system",
    "developer": "system",
    "user": "user",
    "assistant": "retrieved",
    "tool": "tool",
    "function": "tool",
}
MAX_BODY_BYTES = 8 * 1024 * 1024


class ProxyError(Exception):
    """Carries the HTTP status the client should see."""

    def __init__(self, status: int, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    upstream_base_url: str
    policy_path: str | None = None
    mode: str = "interactive"
    timeout_seconds: float = 60.0
    forward_authorization: bool = True

    def __post_init__(self) -> None:
        if not self.upstream_base_url.startswith(("http://", "https://")):
            raise ValueError("upstream_base_url must be an absolute http(s) URL")
        if self.mode not in {"interactive", "thorough"}:
            raise ValueError("mode must be 'interactive' or 'thorough'")


def _text_of(content: Any) -> str:
    """OpenAI 는 문자열과 파트 배열 둘 다 허용한다. 텍스트 파트만 검사 대상이다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _with_text(content: Any, replacement: str) -> Any:
    """마스킹 결과를 원래 모양으로 되돌려 넣는다 — 파트 배열이면 첫 텍스트 파트에."""
    if isinstance(content, str) or not isinstance(content, list):
        return replacement
    updated: list[Any] = []
    injected = False
    for item in content:
        if not injected and isinstance(item, dict) and item.get("type") == "text":
            updated.append({**item, "text": replacement})
            injected = True
        else:
            updated.append(item)
    return updated if injected else replacement


def segments_from_messages(messages: list[dict[str, Any]]) -> list[Segment]:
    """`messages` 배열을 검사 단위로 옮긴다. 순서와 개수는 1:1 로 보존한다."""
    if not isinstance(messages, list) or not messages:
        raise ProxyError(400, "messages must be a non-empty array")
    segments: list[Segment] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ProxyError(400, f"messages[{index}] is not an object")
        role = str(message.get("role", ""))
        origin = ROLE_TO_ORIGIN.get(role)
        if origin is None:
            # 모르는 역할을 `user` 로 접으면 신뢰 등급을 임의로 올리는 셈이다.
            raise ProxyError(400, f"messages[{index}] has an unsupported role: {role!r}")
        segments.append(Segment(_text_of(message.get("content")), origin=origin))
    return segments


def _blocked_body(result: Any) -> dict[str, Any]:
    return {
        "error": {
            "type": "garim_blocked",
            "message": "가림이 이 요청을 차단했습니다. 상류로 전달하지 않았습니다.",
            "verdict": result.verdict,
            "degraded": result.degraded,
            "degraded_reasons": result.degraded_reasons,
            "findings": [
                {"code": finding.code, "segment": finding.segment_idx, "rule": finding.rule}
                for finding in (*result.leaks, *result.attacks)
            ],
        }
    }


class ProxyService:
    """검사 → (허용 시) 상류 전달 → 복원. 전송은 `sender` 로 주입해 테스트한다."""

    def __init__(
        self,
        config: ProxyConfig,
        *,
        inspector: Inspector | None = None,
        sender: Any = None,
    ) -> None:
        self.config = config
        if inspector is not None:
            self._inspector_factory = lambda: inspector
        else:
            self._inspector_factory = lambda: Inspector(config.policy_path)
        self._sender = sender or self._send_upstream

    def _send_upstream(self, path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        url = self.config.upstream_base_url.rstrip("/") + path
        request = urllib.request.Request(  # noqa: S310 - 사용자가 선언한 상류다
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=self.config.timeout_seconds, context=https_context()
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProxyError(exc.code, "upstream rejected the request") from exc
        except OSError as exc:
            raise ProxyError(502, f"upstream is unreachable: {exc}") from exc

    def handle_chat_completions(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        if payload.get("stream"):
            # 조각난 응답에서 볼트 토큰이 청크 경계에 걸리면 복원이 조용히 깨진다.
            raise ProxyError(400, "streaming is not supported: masked tokens cannot be restored")

        segments = segments_from_messages(payload.get("messages"))
        inspector = self._inspector_factory()
        result = inspector.check(segments, mode=self.config.mode)
        response_headers = {
            "X-Garim-Verdict": result.verdict,
            "X-Garim-Degraded": "true" if result.degraded else "false",
        }
        if result.degraded_reasons:
            response_headers["X-Garim-Degraded-Reasons"] = ",".join(result.degraded_reasons)

        if result.verdict == "block":
            return 400, _blocked_body(result), response_headers

        outbound = dict(payload)
        masked = result.masked_segments
        if masked is not None:
            outbound["messages"] = [
                {**message, "content": _with_text(message.get("content"), text)}
                for message, text in zip(payload["messages"], masked, strict=True)
            ]

        upstream = self._sender("/chat/completions", outbound, headers)
        if result.vault is not None:
            upstream = _restore_choices(upstream, result.vault)
        return 200, upstream, response_headers


def _restore_choices(response: Any, vault: Any) -> Any:
    """응답 본문의 볼트 토큰을 원문으로 되돌린다 — §3.3 의 돌아오는 길."""
    if not isinstance(response, dict):
        return response
    choices = response.get("choices")
    if not isinstance(choices, list):
        return response
    restored = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            restored.append(
                {**choice, "message": {**message, "content": vault.restore(message["content"])}}
            )
        else:
            restored.append(choice)
    return {**response, "choices": restored}


class _Handler(BaseHTTPRequestHandler):
    service: ProxyService
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """★ 프롬프트 원문이 접근 로그로 새지 않게 기본 로깅을 끈다(§3.2)."""

    def _reply(self, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self._reply(404, {"error": {"message": "unknown path"}}, {})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(400, {"error": {"message": "invalid Content-Length"}}, {})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(413, {"error": {"message": "request body is missing or too large"}}, {})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": {"message": "body is not valid JSON"}}, {})
            return

        forwarded: dict[str, str] = {}
        authorization = self.headers.get("Authorization")
        if authorization and self.service.config.forward_authorization:
            forwarded["Authorization"] = authorization
        try:
            status, body, headers = self.service.handle_chat_completions(payload, forwarded)
        except ProxyError as exc:
            self._reply(exc.status, {"error": {"message": exc.message, "detail": exc.detail}}, {})
            return
        except Exception as exc:  # 검사 실패를 조용히 통과시키지 않는다(§3.6)
            self._reply(
                500, {"error": {"message": f"garim proxy failed: {type(exc).__name__}"}}, {}
            )
            return
        self._reply(status, body, headers)


def build_server(config: ProxyConfig, *, host: str = "127.0.0.1", port: int = 8080) -> Any:
    """`127.0.0.1` 에만 바인드한다 — 기본값으로 외부에 노출되지 않는다."""
    handler = type("GarimProxyHandler", (_Handler,), {"service": ProxyService(config)})
    return ThreadingHTTPServer((host, port), handler)
