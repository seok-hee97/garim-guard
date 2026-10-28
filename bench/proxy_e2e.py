"""프록시 전 구간 실호출 — 상류 LLM 이 실제로 무엇을 받았는가.

다이어그램의 "통과분만(마스킹된 상태로) → Gemini/OpenAI → 응답 복원" 구간을 실제 모델로
잰다. 테스트는 상류를 가짜로 바꿔 끼우므로 이 구간을 대신하지 못한다.

## 상류가 받은 것을 상류의 입으로 듣는다

모델에게 받은 줄을 두 번 옮겨 적게 한다. 첫 줄은 `<`·`>` 를 `[`·`]` 로 바꿔서, 둘째 줄은
그대로. 프록시는 `<…>` 토큰만 복원하므로

- 첫 줄에 `[PHONE_1]` 이 보이면 상류는 **토큰**을 받았다 — 원문이 나가지 않았다.
- 첫 줄에 원문 번호가 보이면 상류가 **원문**을 받았다 — 유출이다.
- 둘째 줄의 `<PHONE_1>` 은 돌아오는 길에 원문으로 복원돼야 한다.

모델이 지시를 따르지 않으면 판정할 수 없다(`inconclusive`). 그걸 통과로 세지 않는다.

    # 로컬 llama-server 를 상류로 (외부 통신 없음)
    python -m bench.proxy_e2e --upstream http://127.0.0.1:8081/v1 --model gemma-4-e2b-it
    # Gemini (유료 키 — DESIGN §7.4)
    python -m bench.proxy_e2e --upstream https://generativelanguage.googleapis.com/v1beta/openai \\
        --model gemini-3.8-flash --api-key-env GEMINI_API_KEY

값은 전부 합성이다. 그래도 Gemini·OpenAI 를 상류로 쓰면 그 요청은 기기 밖으로 나간다.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

PHONE = "010-2345-6789"
EMAIL = "hong.gildong@example.com"
INSTRUCTION = (
    "아래 줄을 두 번 출력해. 첫 번째는 '<' 를 '[' 로, '>' 를 ']' 로만 바꿔서, 두 번째는 한 글자도 "
    "바꾸지 말고 그대로. 다른 말은 하지 마.\n"
    f"연락처 {PHONE}, 메일 {EMAIL}"
)


def judge_echo(content: str) -> dict[str, Any]:
    """상류의 메아리에서 무엇이 나갔는지 판정한다."""
    bracketed = "[PHONE_1]" in content or "[EMAIL_1]" in content
    lines = [line for line in content.splitlines() if line.strip()]
    first = lines[0] if lines else ""
    raw_in_first = PHONE in first or EMAIL in first
    restored = PHONE in content and EMAIL in content
    if raw_in_first and not bracketed:
        verdict = "leak"
    elif bracketed and restored:
        verdict = "pass"
    else:
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "upstream_saw_tokens": bracketed,
        "raw_value_in_bracketed_line": raw_in_first,
        "restored_on_the_way_back": restored,
    }


def _content_from_sse(text: str) -> str:
    """SSE 청크의 `delta.content` 를 이어 붙인다 — 스트리밍 클라이언트가 보는 문자열."""
    parts = []
    for block in text.split("\n\n"):
        if not block.startswith("data: ") or block == "data: [DONE]":
            continue
        chunk = json.loads(block[len("data: ") :])
        for choice in chunk.get("choices", []):
            parts.append(choice.get("delta", {}).get("content") or "")
    return "".join(parts)


def run(
    upstream: str, model: str, api_key: str | None, timeout: float, *, stream: bool = False
) -> dict[str, Any]:
    from garim.proxy import ProxyConfig, build_server

    server = build_server(ProxyConfig(upstream_base_url=upstream, timeout_seconds=timeout), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "너는 받은 문장을 그대로 옮겨 적는 도우미다."},
                {"role": "user", "content": INSTRUCTION},
            ],
            "temperature": 0,
            "stream": stream,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout + 30) as response:  # noqa: S310
                garim = {k: v for k, v in response.headers.items() if k.startswith("X-Garim")}
                raw = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return {
                "verdict": "error",
                "status": exc.code,
                "body": exc.read(2000).decode("utf-8", errors="replace"),
            }
        if stream:
            if not content_type.startswith("text/event-stream"):
                return {"verdict": "error", "reason": f"expected SSE, got {content_type}"}
            content = _content_from_sse(raw)
        else:
            content = json.loads(raw)["choices"][0]["message"]["content"] or ""
        return {
            "upstream": upstream,
            "model": model,
            "stream": stream,
            "garim_headers": garim,
            "content": content,
            **judge_echo(content),
        }
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="프록시 전 구간 실호출 — 상류가 받은 것")
    parser.add_argument("--upstream", required=True, help="OpenAI 호환 /v1 base URL")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", help="Authorization 으로 넘길 키의 환경 변수 이름")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--stream", action="store_true", help="stream=true 로 SSE 경로를 잰다")
    args = parser.parse_args()
    api_key = None
    if args.api_key_env:
        from bench.mask_quality import load_env

        api_key = {**load_env(".env"), **os.environ}.get(args.api_key_env)
        if not api_key:
            raise SystemExit(f"{args.api_key_env} 가 없다")
    report = run(args.upstream, args.model, api_key, args.timeout, stream=args.stream)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"pass": 0, "leak": 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
