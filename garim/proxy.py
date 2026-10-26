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
3. **스트리밍은 모아서 복원한 뒤 돌려준다(buffered SSE).** 조각난 응답을 조각째 복원하면
   `<PHONE_1>` 같은 토큰이 청크 경계에 걸려 깨진다. 그래서 상류에는 스트리밍 없이 요청하고,
   전문을 복원한 다음 OpenAI `chat.completion.chunk` 형식의 SSE 로 돌려준다. 앱은
   `stream=True` 그대로 동작하고, 대가는 첫 글자가 응답 전체만큼 늦게 오는 것이다.
   ★ 초판은 스트리밍을 거부했다. 채팅 앱 대부분이 `stream=True` 라 그대로면 앱이 깨지고,
   §1.1 이 경고한 대로 도구를 끄게 된다. 거부가 필요하면 `streaming="refuse"`.
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
from garim.detect.l1 import L1Backend
from garim.detect.l2 import L2Backend
from garim.models import Origin, Segment
from garim.policy.schema import load_policy
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
    l1_model_dir: str | None = None
    l1_model_file: str = "garim_l1.int8.onnx"
    streaming: str = "buffered"

    def __post_init__(self) -> None:
        if not self.upstream_base_url.startswith(("http://", "https://")):
            raise ValueError("upstream_base_url must be an absolute http(s) URL")
        if self.streaming not in {"buffered", "refuse"}:
            raise ValueError("streaming must be 'buffered' or 'refuse'")
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
    """마스킹 결과를 원래 모양으로 되돌려 넣는다.

    ★ **원문 유출이 있었다.** `_text_of` 는 텍스트 파트를 **전부** 합쳐 검사에 넘기는데,
      이 함수는 첫 파트만 교체하고 나머지를 **원문 그대로 남겼다.** 그래서 두 번째
      파트에 이메일이 있으면 마스킹본과 원본이 **함께** 상류로 나갔다.

          입력  [{"text": "안녕하세요"}, {"text": "메일은 hong@example.test 입니다"}]
          출력  [{"text": "안녕하세요\n메일은 <EMAIL_1> 입니다"},
                 {"text": "메일은 hong@example.test 입니다"}]   ← 원문

      §1 이 "외부로 아무것도 내보내지 않는다" 를 이 프로젝트의 **유일한 이유이자 가장
      중요한 성질** 이라고 선언했다. 프록시는 그 성질이 사용자 대신 상류를 부를 때도
      유지되는지를 책임지는 자리다.

    ## 합친 것을 다시 쪼개지 않는다

    검사에 넘긴 것은 `"\n"` 으로 합친 **한 덩어리**이고 마스킹도 그 위에서 일어났다.
    되돌릴 때 개행으로 다시 쪼개는 방법이 있지만, 스팬이 파트 경계를 넘으면 조각 수가
    안 맞고 그때 조용히 어긋난다. **첫 텍스트 파트에 전문을 넣고 나머지 텍스트 파트는
    지운다** — 뜻은 그대로이고 원문이 남을 자리가 없다.

    텍스트가 아닌 파트(이미지 등)는 순서대로 보존한다. 검사 대상이 아니었으므로 손댈
    이유가 없다.
    """
    if content is None and replacement == "":
        # `tool_calls` 만 있는 assistant 메시지는 content 가 null 이다. 다른 메시지가
        # 마스킹됐다는 이유로 그 모양을 "" 로 바꿀 이유가 없다.
        return None
    if isinstance(content, str) or not isinstance(content, list):
        return replacement
    updated: list[Any] = []
    injected = False
    for item in content:
        is_text = isinstance(item, dict) and item.get("type") == "text"
        if is_text:
            if injected:
                # 이미 전문을 넣었다. 남겨 두면 그것이 곧 원문 유출이다.
                continue
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


@dataclass(frozen=True, slots=True)
class _ArgumentSlot:
    """`tool_calls[].function.arguments` 하나 — `content` 밖에 있는 검사 단위.

    ★ **원문 유출이 있었다.** `segments_from_messages` 는 `content` 만 읽는다. 함수
      호출을 쓰는 대화는 매 요청에 이전 턴의 assistant `tool_calls` 를 함께 보내는데,
      그 인자가 검사 없이 상류로 나갔다. 같은 요청 안에서 `tool` 메시지의
      `kim@example.test` 는 `<EMAIL_1>` 로 가려지고, 바로 앞 assistant 메시지의
      `{"email": "kim@example.test"}` 는 원문 그대로 나갔다.

    §1.3 은 도구 호출을 범위 밖으로 둔다. 그래도 프록시가 `tool` 역할을 받아들이는
    이상, 그 대화에 실려 가는 바이트를 검사하지 않을 근거는 없다.
    """

    message: int
    call: int | None  # None 이면 구식 `function_call`
    original: str
    scanned: str
    is_json: bool


def _argument_slots(messages: list[dict[str, Any]]) -> list[_ArgumentSlot]:
    slots: list[_ArgumentSlot] = []
    for index, message in enumerate(messages):
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ProxyError(400, f"messages[{index}].tool_calls must be an array")
        for call_index, call in enumerate(calls or []):
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and "arguments" in function:
                slots.append(_slot(index, call_index, function["arguments"]))
        legacy = message.get("function_call")
        if isinstance(legacy, dict) and "arguments" in legacy:
            slots.append(_slot(index, None, legacy["arguments"]))
    return slots


def _slot(message: int, call: int | None, arguments: Any) -> _ArgumentSlot:
    if not isinstance(arguments, str):
        # OpenAI 계약은 JSON 문자열이다. 다른 모양을 추측해 직렬화하면 상류가 받는
        # 바이트와 우리가 검사한 바이트가 갈린다.
        raise ProxyError(400, f"messages[{message}] tool call arguments must be a JSON string")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return _ArgumentSlot(message, call, arguments, arguments, is_json=False)
    # `\uXXXX` 이스케이프를 풀어 검사한다 — 기본 `json.dumps` 는 한글을 전부 이스케이프하고
    # 규칙은 이스케이프된 바이트를 읽지 못한다.
    scanned = json.dumps(parsed, ensure_ascii=False)
    return _ArgumentSlot(message, call, arguments, scanned, is_json=True)


def _masked_arguments(slot: _ArgumentSlot, masked: str) -> str:
    if masked == slot.scanned:
        return slot.original
    if slot.is_json:
        try:
            json.loads(masked)
        except json.JSONDecodeError as exc:
            # 스팬이 JSON 이스케이프에 걸쳤다. 깨진 인자를 보내느니 보내지 않는다.
            raise ProxyError(
                400, "masking would corrupt tool call arguments; request was not forwarded"
            ) from exc
    return masked


def _with_arguments(
    messages: list[dict[str, Any]], slots: list[_ArgumentSlot], masked: list[str]
) -> list[dict[str, Any]]:
    updated = [dict(message) for message in messages]
    for slot, text in zip(slots, masked, strict=True):
        arguments = _masked_arguments(slot, text)
        if arguments is slot.original:
            continue
        message = updated[slot.message]
        if slot.call is None:
            message["function_call"] = {**message["function_call"], "arguments": arguments}
            continue
        calls = list(message["tool_calls"])
        call = calls[slot.call]
        calls[slot.call] = {**call, "function": {**call["function"], "arguments": arguments}}
        message["tool_calls"] = calls
    return updated


def _blocked_body(result: Any, segment_messages: list[int]) -> dict[str, Any]:
    return {
        "error": {
            "type": "garim_blocked",
            "message": "가림이 이 요청을 차단했습니다. 상류로 전달하지 않았습니다.",
            "verdict": result.verdict,
            "degraded": result.degraded,
            "degraded_reasons": result.degraded_reasons,
            "findings": [
                {
                    "code": finding.code,
                    "segment": finding.segment_idx,
                    "message": segment_messages[finding.segment_idx],
                    "rule": finding.rule,
                }
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
        l1_backend: L1Backend | None = None,
        l2_backend: L2Backend | None = None,
    ) -> None:
        self.config = config
        if inspector is not None:
            self._inspector_factory = lambda: inspector
        else:
            # 모델은 프로세스에 한 번만 올린다. 볼트는 요청마다 새로 만들어야 하므로
            # (다른 요청의 원문이 이 응답에 복원되면 안 된다) Inspector 는 요청마다,
            # 백엔드는 공유다. 공유를 견디는 조건은 `OnnxL1Backend` 가 진다.
            if l1_backend is None and config.l1_model_dir is not None:
                from garim.detect.l1 import OnnxL1Backend

                l1_backend = OnnxL1Backend(
                    config.l1_model_dir, model_filename=config.l1_model_file
                )

            def trusted_inspector() -> Inspector:
                policy = load_policy(config.policy_path)
                # Unlike the library API, the proxy parsed the protocol-level role itself.
                # This is the one place where DESIGN §2.5b allows origin trust.
                policy.origin_policy.trust_declared = True
                return Inspector(policy, l1_backend=l1_backend, l2_backend=l2_backend)

            self._inspector_factory = trusted_inspector
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
            # 상류의 오류 본문을 넘긴다 — 없으면 할당량 초과(429)·과부하(503)·키 오류를
            # 앱에서 구분할 수 없다. 상류가 받은 것은 이미 마스킹된 요청이므로 그 응답에
            # 원문이 실릴 자리는 없다.
            raw = exc.read(4096)
            try:
                detail: Any = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = raw.decode("utf-8", errors="replace")[:1000]
            raise ProxyError(exc.code, "upstream rejected the request", detail) from exc
        except OSError as exc:
            raise ProxyError(502, f"upstream is unreachable: {exc}") from exc

    def handle_chat_completions(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        stream = bool(payload.get("stream"))
        if stream and self.config.streaming == "refuse":
            # 조각난 응답에서 볼트 토큰이 청크 경계에 걸리면 복원이 조용히 깨진다.
            raise ProxyError(400, "streaming is not supported: masked tokens cannot be restored")

        messages = payload.get("messages")
        segments = segments_from_messages(messages)
        slots = _argument_slots(messages)
        segments.extend(
            Segment(slot.scanned, origin=segments[slot.message].origin, content_type="structured")
            for slot in slots
        )
        segment_messages = [*range(len(messages)), *(slot.message for slot in slots)]
        inspector = self._inspector_factory()
        result = inspector.check(segments, mode=self.config.mode)
        response_headers = {
            "X-Garim-Verdict": result.verdict,
            "X-Garim-Degraded": "true" if result.degraded else "false",
        }
        if result.degraded_reasons:
            response_headers["X-Garim-Degraded-Reasons"] = ",".join(result.degraded_reasons)

        if result.verdict == "block":
            return 400, _blocked_body(result, segment_messages), response_headers

        outbound = dict(payload)
        masked = result.masked_segments
        if masked is not None:
            outbound["messages"] = _with_arguments(
                [
                    {**message, "content": _with_text(message.get("content"), text)}
                    for message, text in zip(messages, masked[: len(messages)], strict=True)
                ],
                slots,
                masked[len(messages) :],
            )

        if stream:
            # 상류에는 스트리밍 없이 묻는다 — 전문을 복원한 뒤 SSE 로 다시 쪼갠다(`to_sse`).
            outbound["stream"] = False
            outbound.pop("stream_options", None)
        upstream = self._sender("/chat/completions", outbound, headers)
        if result.vault is not None:
            upstream, restore_missing = _restore_choices(upstream, result.vault)
            if restore_missing:
                response_headers["X-Garim-Verdict"] = "warn"
                response_headers["X-Garim-Degraded"] = "true"
                existing = response_headers.get("X-Garim-Degraded-Reasons")
                reasons = [reason for reason in (existing or "").split(",") if reason]
                if "vault_restore_miss" not in reasons:
                    reasons.append("vault_restore_miss")
                response_headers["X-Garim-Degraded-Reasons"] = ",".join(reasons)
        return 200, upstream, response_headers


def _restore_choices(response: Any, vault: Any) -> tuple[Any, bool]:
    """응답 본문의 볼트 토큰을 원문으로 되돌린다 — §3.3 의 돌아오는 길."""
    if not isinstance(response, dict):
        return response, False
    choices = response.get("choices")
    if not isinstance(choices, list):
        return response, False
    restored = []
    missing = False
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            restored.append(choice)
            continue
        message = dict(message)
        if isinstance(message.get("content"), str):
            message["content"], content_missing = vault.restore_with_status(message["content"])
            missing = missing or content_missing
        # ★ 인자도 돌아오는 길이다. 초판은 `content` 만 복원해서, 모델이 마스킹된 번호로
        #   함수를 부르면 앱의 도구가 `{"to": "<PHONE_1>"}` 을 받았다.
        if isinstance(message.get("tool_calls"), list):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    arguments, call_missing = _restore_arguments(function["arguments"], vault)
                    missing = missing or call_missing
                    call = {**call, "function": {**function, "arguments": arguments}}
                calls.append(call)
            message["tool_calls"] = calls
        legacy = message.get("function_call")
        if isinstance(legacy, dict) and isinstance(legacy.get("arguments"), str):
            arguments, call_missing = _restore_arguments(legacy["arguments"], vault)
            missing = missing or call_missing
            message["function_call"] = {**legacy, "arguments": arguments}
        restored.append({**choice, "message": message})
    return {**response, "choices": restored}, missing


def to_sse(response: dict[str, Any], *, include_usage: bool = False) -> str:
    """복원이 끝난 `chat.completion` 하나를 OpenAI `chat.completion.chunk` SSE 로 바꾼다.

    선택지마다 역할 → 본문·함수 호출 → 종료 사유 순서의 청크를 내고 `[DONE]` 으로 끝낸다.
    본문은 한 청크다 — 이미 전문이 손에 있고, 쪼개는 것은 모양만 흉내 낼 뿐이다.
    """
    base = {
        "id": response.get("id", "chatcmpl-garim"),
        "object": "chat.completion.chunk",
        "created": response.get("created", 0),
        "model": response.get("model", ""),
    }
    if response.get("system_fingerprint") is not None:
        base["system_fingerprint"] = response["system_fingerprint"]
    events: list[dict[str, Any]] = []
    for position, choice in enumerate(response.get("choices") or []):
        index = choice.get("index", position) if isinstance(choice, dict) else position
        message = choice.get("message") if isinstance(choice, dict) else None
        message = message if isinstance(message, dict) else {}
        events.append({"index": index, "delta": {"role": message.get("role", "assistant")},
                       "finish_reason": None})
        delta: dict[str, Any] = {}
        if isinstance(message.get("content"), str):
            delta["content"] = message["content"]
        if isinstance(message.get("tool_calls"), list):
            delta["tool_calls"] = [
                {"index": call_index, **call}
                for call_index, call in enumerate(message["tool_calls"])
            ]
        if isinstance(message.get("function_call"), dict):
            delta["function_call"] = message["function_call"]
        if delta:
            events.append({"index": index, "delta": delta, "finish_reason": None})
        events.append({"index": index, "delta": {},
                       "finish_reason": choice.get("finish_reason", "stop")})
    lines = [
        "data: " + json.dumps({**base, "choices": [event]}, ensure_ascii=False) + "\n\n"
        for event in events
    ]
    if include_usage and response.get("usage") is not None:
        lines.append(
            "data: "
            + json.dumps({**base, "choices": [], "usage": response["usage"]}, ensure_ascii=False)
            + "\n\n"
        )
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def _restore_arguments(arguments: str, vault: Any) -> tuple[str, bool]:
    """JSON 인자는 문자열 리프만 복원하고 다시 직렬화한다.

    원문을 JSON 텍스트에 그대로 끼워 넣으면 따옴표·역슬래시가 든 값이 인자를 깨뜨린다.
    """
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return vault.restore_with_status(arguments)
    missing = False
    changed = False

    def walk(node: Any) -> Any:
        nonlocal missing, changed
        if isinstance(node, str):
            value, node_missing = vault.restore_with_status(node)
            missing = missing or node_missing
            changed = changed or value != node
            return value
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {walk(key): walk(value) for key, value in node.items()}
        return node

    restored = walk(parsed)
    if not changed:
        return arguments, missing
    return json.dumps(restored, ensure_ascii=False), missing


class _Handler(BaseHTTPRequestHandler):
    service: ProxyService
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """★ 프롬프트 원문이 접근 로그로 새지 않게 기본 로깅을 끈다(§3.2)."""

    def _reply_sse(self, body: str, headers: dict[str, str]) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

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
        if status == 200 and payload.get("stream"):
            options = payload.get("stream_options")
            include_usage = isinstance(options, dict) and bool(options.get("include_usage"))
            self._reply_sse(to_sse(body, include_usage=include_usage), headers)
            return
        self._reply(status, body, headers)


def build_server(
    config: ProxyConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    l1_backend: L1Backend | None = None,
    l2_backend: L2Backend | None = None,
) -> Any:
    """`127.0.0.1` 에만 바인드한다 — 기본값으로 외부에 노출되지 않는다."""
    service = ProxyService(config, l1_backend=l1_backend, l2_backend=l2_backend)
    handler = type("GarimProxyHandler", (_Handler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)
