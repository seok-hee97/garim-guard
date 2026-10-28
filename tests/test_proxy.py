"""§8.1-17 로컬 프록시. §8.4-2 가 절삭을 금지한 항목이라 계약을 테스트로 못박는다."""

from __future__ import annotations

from typing import Any

import pytest

from garim.proxy import (
    ProxyConfig,
    ProxyError,
    ProxyService,
    segments_from_messages,
)


class _Recorder:
    """상류로 실제로 나간 것을 붙잡는다 — 차단이 전달되지 않았음을 보는 유일한 방법."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append({"path": path, "payload": payload, "headers": headers})
        last = payload["messages"][-1]["content"]
        return {"choices": [{"message": {"role": "assistant", "content": f"받음: {last}"}}]}


def _service(sender: Any) -> ProxyService:
    return ProxyService(ProxyConfig(upstream_base_url="https://upstream.test/v1"), sender=sender)


def _request(*messages: tuple[str, str]) -> dict[str, Any]:
    return {"model": "x", "messages": [{"role": r, "content": c} for r, c in messages]}


def test_config_refuses_a_relative_upstream() -> None:
    """상류 주소는 항상 사용자의 명시적 선언이어야 한다 — 코드에 박힌 기본값이 없다."""
    with pytest.raises(ValueError, match="absolute http"):
        ProxyConfig(upstream_base_url="api.openai.com/v1")


def test_roles_map_to_origins_without_flattening_tool_output() -> None:
    segments = segments_from_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "t"},
        ]
    )
    assert [item.origin for item in segments] == ["system", "user", "retrieved", "tool"]


def test_unknown_role_is_refused_not_downgraded_to_user() -> None:
    """모르는 역할을 `user` 로 접으면 신뢰 등급을 임의로 올리는 셈이다."""
    with pytest.raises(ProxyError, match="unsupported role"):
        segments_from_messages([{"role": "moderator", "content": "x"}])


def test_masked_prompt_goes_upstream_and_the_answer_comes_back_restored() -> None:
    recorder = _Recorder()
    status, body, headers = _service(recorder).handle_chat_completions(
        _request(
            ("system", "상담 도우미입니다."),
            ("user", "제 이메일은 hong@example.test 입니다"),
        ),
        {},
    )
    assert status == 200
    assert headers["X-Garim-Verdict"] == "mask"
    sent = recorder.calls[0]["payload"]["messages"][-1]["content"]
    assert "hong@example.test" not in sent
    assert "<EMAIL_1>" in sent
    # 돌아오는 길에서만 복원한다(§3.3).
    assert "hong@example.test" in body["choices"][0]["message"]["content"]


def test_block_is_never_forwarded() -> None:
    recorder = _Recorder()
    status, body, headers = _service(recorder).handle_chat_completions(
        _request(("user", "주민등록번호 900101-1234567 확인해줘")), {}
    )
    assert status == 400
    assert headers["X-Garim-Verdict"] == "block"
    assert recorder.calls == [], "차단된 요청이 상류로 나갔다"
    assert "L-KR-01" in {item["code"] for item in body["error"]["findings"]}


def test_message_count_and_order_are_preserved() -> None:
    """`masked_segments` 는 입력과 1:1 이다(§3.2.2) — 프록시가 그 계약의 근거다."""
    recorder = _Recorder()
    request = _request(
        ("system", "규칙입니다."),
        ("user", "번호는 010-1234-5678 입니다"),
        ("assistant", "확인했습니다."),
        ("user", "감사합니다."),
    )
    _service(recorder).handle_chat_completions(request, {})
    sent = recorder.calls[0]["payload"]["messages"]
    assert len(sent) == 4
    assert [item["role"] for item in sent] == ["system", "user", "assistant", "user"]
    assert sent[0]["content"] == "규칙입니다."
    assert sent[3]["content"] == "감사합니다."


def test_content_part_arrays_keep_their_shape() -> None:
    recorder = _Recorder()
    request = {
        "model": "x",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "카드 4539578763621486 확인"},
                    {"type": "image_url", "image_url": {"url": "https://x.test/a.png"}},
                ],
            }
        ],
    }
    _service(recorder).handle_chat_completions(request, {})
    content = recorder.calls[0]["payload"]["messages"][0]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert "4539578763621486" not in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_refuse_mode_still_refuses_streaming() -> None:
    """`--refuse-streaming` — 거부가 필요한 배치는 그대로 고를 수 있다."""
    recorder = _Recorder()
    service = ProxyService(
        ProxyConfig(upstream_base_url="https://upstream.test/v1", streaming="refuse"),
        sender=recorder,
    )
    with pytest.raises(ProxyError, match="streaming is not supported"):
        service.handle_chat_completions({**_request(("user", "안녕")), "stream": True}, {})
    assert recorder.calls == []


def _serve(sender: Any, **config: Any) -> Any:
    """실제 HTTP 핸들러로 띄운다 — SSE 는 핸들러가 만든다."""
    import threading
    from http.server import ThreadingHTTPServer

    from garim.proxy import _Handler

    service = ProxyService(
        ProxyConfig(upstream_base_url="https://upstream.test/v1", **config), sender=sender
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_Handler,), {"service": service}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _post(server: Any, body: dict[str, Any]) -> tuple[int, dict[str, str], str]:
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def _sse_chunks(text: str) -> list[Any]:
    import json

    events = [line[len("data: "):] for line in text.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return [json.loads(event) for event in events[:-1]]


def test_streaming_is_answered_as_sse_after_full_restoration() -> None:
    """★ 회귀 — 초판은 `stream=True` 를 거부했다. 채팅 앱 대부분이 스트리밍이라 앱이 깨졌다.

    상류에는 스트리밍 없이 묻고(토큰이 청크 경계에 걸리지 않게), 전문을 복원해 SSE 로 준다.
    """
    seen: list[dict[str, Any]] = []

    def sender(path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        seen.append(payload)
        token = payload["messages"][-1]["content"].split()[1]
        return {
            "id": "chatcmpl-1",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"{token}로 연락드릴게요"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        }

    server = _serve(sender)
    try:
        status, headers, text = _post(
            server,
            {
                **_request(("user", "번호 010-1234-5678 입니다")),
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert headers["X-Garim-Verdict"] == "mask"
    assert seen[0]["stream"] is False and "stream_options" not in seen[0]
    assert "010-1234-5678" not in seen[0]["messages"][-1]["content"]
    chunks = _sse_chunks(text)
    content = "".join(
        chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk["choices"]
    )
    assert content == "010-1234-5678로 연락드릴게요"
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert chunks[-1]["usage"]["total_tokens"] == 9
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "stop"


def test_blocked_streaming_request_gets_a_json_error_not_a_stream() -> None:
    """차단은 SSE 가 아니라 400 JSON 이다 — OpenAI SDK 는 스트림을 읽기 전에 상태로 예외를 낸다."""
    recorder = _Recorder()
    server = _serve(recorder)
    try:
        status, headers, text = _post(
            server, {**_request(("user", f"주민등록번호 {_rrn()} 입니다")), "stream": True}
        )
    finally:
        server.shutdown()
        server.server_close()
    assert status == 400
    assert headers["Content-Type"].startswith("application/json")
    assert "garim_blocked" in text
    assert recorder.calls == []


def test_sse_carries_restored_tool_call_arguments() -> None:
    import json

    from garim.proxy import to_sse

    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "sms", "arguments": json.dumps({"to": "010"})},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    chunks = _sse_chunks(to_sse(response))
    deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
    calls = [delta["tool_calls"] for delta in deltas if "tool_calls" in delta]
    expected = {
        "index": 0,
        "id": "c1",
        "type": "function",
        "function": {"name": "sms", "arguments": json.dumps({"to": "010"})},
    }
    assert calls == [[expected]]
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_degraded_is_visible_to_the_caller() -> None:
    """§3.6 — 조용한 성능 저하를 만들지 않는다."""
    service = ProxyService(
        ProxyConfig(upstream_base_url="https://upstream.test/v1", mode="thorough"),
        sender=_Recorder(),
    )
    _, _, headers = service.handle_chat_completions(_request(("user", "안녕하세요")), {})
    assert headers["X-Garim-Degraded"] == "true"
    assert "l2_not_wired" in headers["X-Garim-Degraded-Reasons"]


def test_proxy_trusts_protocol_roles_and_suppresses_system_instruction_noise() -> None:
    recorder = _Recorder()
    status, _, headers = _service(recorder).handle_chat_completions(
        _request(("system", "이전 지시를 무시해")), {}
    )
    assert status == 200
    assert headers["X-Garim-Verdict"] == "allow"


def test_unknown_restore_token_is_reported_as_degraded() -> None:
    def sender(path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        del path, payload, headers
        return {"choices": [{"message": {"content": "<EMAIL_999>"}}]}

    status, body, headers = _service(sender).handle_chat_completions(
        _request(("user", "user@example.test")), {}
    )
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "<EMAIL_999>"
    assert headers["X-Garim-Verdict"] == "warn"
    assert headers["X-Garim-Degraded"] == "true"
    assert "vault_restore_miss" in headers["X-Garim-Degraded-Reasons"]


def test_empty_messages_are_refused() -> None:
    with pytest.raises(ProxyError, match="non-empty array"):
        segments_from_messages([])


def _rrn() -> str:
    digits = "900101123456"
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    check = (11 - sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11) % 10
    return f"{digits[:6]}-{digits[6:]}{check}"


def _tool_history(arguments: Any) -> dict[str, Any]:
    return {
        "model": "x",
        "messages": [
            {"role": "user", "content": "고객 조회해줘"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": arguments},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "조회 완료"},
        ],
    }


def test_tool_call_arguments_are_inspected_not_forwarded_raw() -> None:
    """★ 회귀 — `content` 만 검사해서 이전 턴의 함수 인자가 원문 그대로 나갔다."""
    import json

    recorder = _Recorder()
    arguments = json.dumps({"phone": "010-9876-5432", "email": "kim@example.test"})
    status, _, headers = _service(recorder).handle_chat_completions(_tool_history(arguments), {})
    assert status == 200
    assert headers["X-Garim-Verdict"] == "mask"
    outbound = recorder.calls[0]["payload"]
    serialized = json.dumps(outbound, ensure_ascii=False)
    assert "010-9876-5432" not in serialized
    assert "kim@example.test" not in serialized
    sent = json.loads(outbound["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert sent == {"phone": "<PHONE_1>", "email": "<EMAIL_1>"}
    # 손대지 않은 모양은 그대로다.
    assert outbound["messages"][1]["content"] is None


def test_tool_call_arguments_are_inspected_after_unescaping_hangul() -> None:
    """기본 `json.dumps` 는 한글을 `\\uXXXX` 로 낸다 — 이스케이프된 채로는 규칙이 못 읽는다."""
    import json

    from garim.proxy import _field_leaves, _working_copy

    arguments = json.dumps({"memo": "[대외비] 구조조정 계획"})
    assert "대외비" not in arguments
    leaves = _field_leaves(_working_copy(_tool_history(arguments)))
    assert any("대외비" in leaf.text for leaf in leaves)


def test_blocked_tool_call_arguments_are_never_forwarded() -> None:
    import json

    recorder = _Recorder()
    arguments = json.dumps({"rrn": f"주민등록번호 {_rrn()}"})
    status, body, _ = _service(recorder).handle_chat_completions(_tool_history(arguments), {})
    assert status == 400
    assert recorder.calls == []
    finding = next(item for item in body["error"]["findings"] if item["code"] == "L-KR-01")
    assert finding["message"] == 1


def test_non_string_tool_arguments_are_refused() -> None:
    with pytest.raises(ProxyError, match="JSON string"):
        _service(_Recorder()).handle_chat_completions(_tool_history({"phone": "x"}), {})


def test_tool_call_arguments_in_the_answer_are_restored() -> None:
    """★ 회귀 — 응답은 `content` 만 복원해서 앱의 도구가 `<PHONE_1>` 을 받았다."""
    import json

    def sender(path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        del path, headers
        token = payload["messages"][0]["content"].split()[0]
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {"name": "sms", "arguments": json.dumps({"to": token})},
                            }
                        ],
                    }
                }
            ]
        }

    status, body, headers = _service(sender).handle_chat_completions(
        _request(("user", "010-1234-5678 로 문자 보내줘")), {}
    )
    assert status == 200
    arguments = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"to": "010-1234-5678"}
    assert headers["X-Garim-Degraded"] == "false"


def test_upstream_error_body_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 할당량·503 과부하·키 오류를 앱이 구분할 수 있어야 한다 — 초판은 본문을 버렸다."""
    import io
    import urllib.error
    import urllib.request

    def refuse(*args: Any, **kwargs: Any) -> Any:
        body = io.BytesIO(b'{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}')
        raise urllib.error.HTTPError("https://upstream.test/v1", 429, "Too Many", {}, body)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    service = ProxyService(ProxyConfig(upstream_base_url="https://upstream.test/v1"))
    with pytest.raises(ProxyError) as caught:
        service.handle_chat_completions(_request(("user", "안녕하세요")), {})
    assert caught.value.status == 429
    assert caught.value.detail == {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}


def test_e2e_echo_judge_tells_a_leak_from_a_pass_and_refuses_to_guess() -> None:
    """상류의 메아리로 판정한다 — 지시를 안 따른 응답을 통과로 세지 않는다."""
    from bench.proxy_e2e import EMAIL, PHONE, judge_echo

    passed = judge_echo(f"연락처 [PHONE_1], 메일 [EMAIL_1]\n연락처 {PHONE}, 메일 {EMAIL}")
    assert passed["verdict"] == "pass"
    leaked = judge_echo(f"연락처 {PHONE}, 메일 {EMAIL}\n연락처 {PHONE}, 메일 {EMAIL}")
    assert leaked["verdict"] == "leak"
    assert judge_echo("죄송하지만 도와드릴 수 없습니다.")["verdict"] == "inconclusive"


def test_lone_surrogate_does_not_open_a_path_upstream() -> None:
    """★ JSON 의 `\\ud800` 한 글자로 세그먼트 검사를 건너뛰게 할 수 있었다.

    초판은 인코딩 오류 세그먼트를 검사하지 않고 warn 으로만 올렸고, warn 은 전달된다 —
    엄격 프로필에서도 API 키와 이메일이 원문으로 나갔다.
    """
    import json

    recorder = _Recorder()
    key = "sk-proj-" + "A" * 40
    body = json.loads(
        '{"model": "x", "messages": [{"role": "user", "content": "key ' + key
        + ' mail kim@example.test \\ud800"}]}'
    )
    status, _, headers = _service(recorder).handle_chat_completions(body, {})
    assert status == 400
    assert recorder.calls == []
    assert "input_encoding_error" in headers["X-Garim-Degraded-Reasons"]

    masked_only = json.loads(
        '{"model": "x", "messages": [{"role": "user", "content": "메일 kim@example.test \\ud800"}]}'
    )
    status, _, headers = _service(recorder).handle_chat_completions(masked_only, {})
    assert status == 200
    sent = json.dumps(recorder.calls[-1]["payload"], ensure_ascii=False)
    assert "kim@example.test" not in sent
    assert headers["X-Garim-Verdict"] == "warn"


def test_fields_outside_message_content_are_inspected() -> None:
    """★ `content` · 도구 인자 밖의 필드가 `dict(payload)` 로 그대로 나갔다."""
    import json

    recorder = _Recorder()
    payload = {
        "model": "x",
        "temperature": 0.2,
        "messages": [
            {"role": "user", "content": "안녕하세요", "name": "lee@example.test"},
            {"role": "tool", "tool_call_id": "call_8fA9b2Kx0Qz7LmN3", "content": "ok"},
        ],
        "prediction": {"type": "content", "content": "연락처 010-2222-3333"},
        "tools": [
            {"type": "function", "function": {"name": "f", "description": "메일 kim@example.test"}}
        ],
        "user": "park@example.test",
    }
    status, _, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200
    sent = recorder.calls[0]["payload"]
    text = json.dumps(sent, ensure_ascii=False)
    for value in ("lee@example.test", "010-2222-3333", "kim@example.test", "park@example.test"):
        assert value not in text
    # 검사 대상이 아닌 값과 ID 는 그대로다.
    assert sent["model"] == "x" and sent["temperature"] == 0.2
    assert sent["messages"][1]["tool_call_id"] == "call_8fA9b2Kx0Qz7LmN3"
    assert sent["tools"][0]["function"]["name"] == "f"


def test_block_in_a_request_field_is_never_forwarded() -> None:
    recorder = _Recorder()
    payload = {
        **_request(("user", "안녕하세요")),
        "prediction": {"type": "content", "content": "주민등록번호 900101-1234561"},
    }
    status, body, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 400
    assert recorder.calls == []
    assert body["error"]["findings"][0]["message"] is None  # 메시지가 아니라 요청 필드다


def test_image_only_message_keeps_its_parts_when_another_message_is_masked() -> None:
    """★ 텍스트 파트가 없는 배열을 "" 로 바꿔 이미지가 지워졌다."""
    recorder = _Recorder()
    image = [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]
    payload = {
        "model": "x",
        "messages": [
            {"role": "user", "content": image},
            {"role": "user", "content": "메일 kim@example.test"},
        ],
    }
    _service(recorder).handle_chat_completions(payload, {})
    assert recorder.calls[0]["payload"]["messages"][0]["content"] == image


def test_text_parts_are_inspected_whatever_their_type_name() -> None:
    recorder = _Recorder()
    payload = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [{"type": "input_text", "text": "메일 kim@example.test"}]}
        ],
    }
    _service(recorder).handle_chat_completions(payload, {})
    sent = recorder.calls[0]["payload"]["messages"][0]["content"]
    assert sent == [{"type": "input_text", "text": "메일 <EMAIL_1>"}]


def test_unread_body_is_not_parsed_as_the_next_request() -> None:
    """★ 본문을 읽지 않은 404 뒤에 keep-alive 로 남아, 본문 바이트가 다음 요청이 됐다."""
    import socket

    server = _serve(_Recorder())
    try:
        smuggled = b"POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}"
        raw = (
            b"POST /v1/unknown HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(smuggled)
            + smuggled
        )
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as sock:
            sock.sendall(raw)
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        assert data.count(b"HTTP/1.1 ") == 1
        assert data.startswith(b"HTTP/1.1 404")
    finally:
        server.shutdown()


def test_non_object_body_is_a_client_error_not_a_crash() -> None:
    server = _serve(_Recorder())
    try:
        status, _, text = _post(server, [1, 2])  # type: ignore[arg-type]
        assert status == 400
        assert "JSON object" in text
    finally:
        server.shutdown()


def test_strings_inside_parts_and_custom_tool_calls_are_inspected() -> None:
    """★ 2차 검토 — 텍스트 파트 · 함수 인자 밖의 문자열이 그대로 나갔다."""
    import json

    recorder = _Recorder()
    payload = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [
                {"type": "file", "file": {"filename": "계약서_hong@example.test.pdf",
                                          "file_data": "data:application/pdf;base64,QUJD"}},
                {"type": "image_url", "image_url": {"url": "https://x.test/a.png?o=kim@example.test"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ]},
            {"role": "assistant", "content": [
                {"type": "refusal", "refusal": "lee@example.test 에게는 보낼 수 없습니다"}
            ], "tool_calls": [
                {"id": "call_8fA9b2Kx0Qz7LmN3", "type": "custom",
                 "custom": {"name": "mail", "input": "to: park@example.test"}}
            ]},
            {"role": "user", "content": "다음"},
        ],
    }
    status, _, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200
    sent = recorder.calls[0]["payload"]
    text = json.dumps(sent, ensure_ascii=False)
    for value in ("hong@example.test", "kim@example.test", "lee@example.test", "park@example.test"):
        assert value not in text
    # 이진 페이로드와 대화 ID 는 손대지 않는다.
    parts = sent["messages"][0]["content"]
    assert parts[0]["file"]["file_data"] == "data:application/pdf;base64,QUJD"
    assert parts[2]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgo="
    assert sent["messages"][1]["tool_calls"][0]["id"] == "call_8fA9b2Kx0Qz7LmN3"


def test_newline_before_an_email_in_a_field_is_masked_not_refused() -> None:
    """★ 2차 검토 — JSON 텍스트째 검사해 `\\n` 의 `n` 이 이메일에 붙고 400 이 났다."""
    recorder = _Recorder()
    arguments = '{"note": "첫 줄\\nkim@example.test"}'
    payload = {
        **_tool_history(arguments),
        "prediction": {"type": "content", "content": "# CODEOWNERS\nhong@example.test\n"},
    }
    status, _, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200
    sent = recorder.calls[0]["payload"]
    assert sent["prediction"]["content"].startswith("# CODEOWNERS\n<EMAIL_")
    assert "hong@example.test" not in sent["prediction"]["content"]
    import json

    forwarded = sent["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(forwarded)["note"].startswith("첫 줄\n<EMAIL_")


def test_large_tool_definitions_get_their_own_input_budget() -> None:
    """★ 2차 검토 — 큰 `tools` 정의와 긴 대화가 한도를 나눠 써서 PII 없는 요청이 차단됐다."""
    recorder = _Recorder()
    tools = [
        {"type": "function", "function": {"name": f"tool_{index}",
                                          "description": "주문 상태를 조회한다. " * 20}}
        for index in range(120)
    ]
    payload = {**_request(("user", "배송 문의드립니다. " * 6000)), "tools": tools}
    status, _, headers = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200, headers
    assert "input_truncated" not in headers.get("X-Garim-Degraded-Reasons", "")


def test_base64_skip_applies_only_to_content_part_payloads() -> None:
    """★ 3차 검토 — 어느 깊이의 `data:` 문자열 · `file_data` 키든 건너뛰어 주민번호가 나갔다."""
    import json

    recorder = _Recorder()
    for arguments in (
        json.dumps({"note": f"data:;base64, 주민번호 {_rrn()}"}),
        json.dumps({"file": {"file_data": f"주민번호 {_rrn()}"}}),
    ):
        status, _, _ = _service(recorder).handle_chat_completions(_tool_history(arguments), {})
        assert status == 400, arguments
    assert recorder.calls == []


def test_scalar_json_tool_arguments_are_inspected_unescaped() -> None:
    """★ 3차 검토 — JSON 문자열 하나인 인자는 이스케이프째 검사돼 깨지거나 비껴갔다."""
    import json

    recorder = _Recorder()
    for arguments in ('"note\\nkim@example.test"', '"kim\\u0040example.test"'):
        status, _, _ = _service(recorder).handle_chat_completions(_tool_history(arguments), {})
        assert status == 200
        forwarded = recorder.calls[-1]["payload"]["messages"][1]["tool_calls"][0]["function"]
        value = json.loads(forwarded["arguments"])
        assert "kim@example.test" not in value and "<EMAIL_" in value


def test_repeated_keys_in_tool_arguments_are_refused() -> None:
    """★ 3차 검토 — `json.loads` 가 앞 값을 버려 검사하지 않은 원래 바이트가 나갔다."""
    recorder = _Recorder()
    arguments = '{"to": "kim@example.test", "to": "x"}'
    with pytest.raises(ProxyError, match="repeat a key"):
        _service(recorder).handle_chat_completions(_tool_history(arguments), {})
    assert recorder.calls == []


def test_deeply_nested_request_is_a_client_error() -> None:
    recorder = _Recorder()
    nested: Any = "kim@example.test"
    for _ in range(2000):
        nested = {"a": nested}
    with pytest.raises(ProxyError, match="nested too deeply"):
        _service(recorder).handle_chat_completions(
            {**_request(("user", "안녕")), "metadata": nested}, {}
        )
    assert recorder.calls == []


def test_blocked_field_finding_does_not_claim_a_message_segment() -> None:
    recorder = _Recorder()
    payload = {
        **_request(("user", "안녕하세요"), ("user", "다음")),
        "tools": [{"type": "function", "function": {"name": "f",
                                                    "description": f"주민등록번호 {_rrn()}"}}],
    }
    status, body, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 400
    finding = next(item for item in body["error"]["findings"] if item["code"] == "L-KR-01")
    assert finding["segment"] is None and finding["message"] is None


def test_pii_used_as_a_json_key_is_masked_in_request_fields() -> None:
    """★ 3차 검토 — 필드 검사가 dict 키를 읽지 않아 키 자리의 번호 · 주소가 그대로 나갔다."""
    import json

    recorder = _Recorder()
    arguments = json.dumps({"contacts": {"010-1234-5678": "x"}}, ensure_ascii=False)
    payload = {**_tool_history(arguments), "metadata": {"kim@example.test": "vip"}}
    status, _, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200
    sent = recorder.calls[0]["payload"]
    text = json.dumps(sent, ensure_ascii=False)
    assert "010-1234-5678" not in text and "kim@example.test" not in text
    forwarded = json.loads(sent["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert list(forwarded["contacts"]) == ["<PHONE_1>"]
    assert list(sent["metadata"])[0].startswith("<EMAIL_")


def test_unknown_top_level_and_message_key_names_are_inspected() -> None:
    import json

    recorder = _Recorder()
    payload = {**_request(("user", "안녕")), "kim@example.test": 1}
    payload["messages"][0]["lee@example.test"] = True
    status, _, _ = _service(recorder).handle_chat_completions(payload, {})
    assert status == 200
    text = json.dumps(recorder.calls[0]["payload"], ensure_ascii=False)
    assert "kim@example.test" not in text and "lee@example.test" not in text
