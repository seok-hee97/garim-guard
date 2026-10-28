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

import base64
import copy
import json
import math
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from garim.api import Inspector
from garim.attachments import Extraction, extract_document
from garim.detect.l1 import L1Backend
from garim.detect.l2 import L2Backend
from garim.models import CheckResult, Origin, Segment
from garim.policy.engine import SEVERITY
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
        if not (math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0):
            # ★ `-1` · `nan` 도 받고 떠서, 모든 요청이 상류 호출에서 실패했다.
            raise ValueError("timeout_seconds must be a positive number")


def _is_text_part(item: Any) -> bool:
    """`text` 문자열을 가진 파트는 타입 이름과 상관없이 텍스트로 본다.

    `type == "text"` 만 보면 `input_text` 처럼 이름만 다른 파트가 검사 없이 나간다.
    """
    return isinstance(item, dict) and isinstance(item.get("text"), str)


def _text_of(content: Any) -> str:
    """OpenAI 는 문자열과 파트 배열 둘 다 허용한다. 텍스트 파트만 검사 대상이다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item["text"] for item in content if _is_text_part(item)]
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

    텍스트가 아닌 파트(이미지 등)는 순서대로 보존한다. 그 안의 문자열(파일 이름 · URL)은
    필드 검사(`_field_leaves`)가 이미 가렸다.
    """
    if isinstance(content, str):
        return replacement
    if not isinstance(content, list):
        # `tool_calls` 만 있는 assistant 메시지의 null, 또는 객체 — 본문 검사 대상이 아니었다.
        # 다른 메시지가 마스킹됐다는 이유로 모양을 "" 로 바꿀 이유가 없다. 객체 안의 문자열은
        # 필드 검사(`_field_leaves`)가 이미 가렸다.
        return content
    updated: list[Any] = []
    injected = False
    for item in content:
        if _is_text_part(item):
            if injected:
                # 이미 전문을 넣었다. 남겨 두면 그것이 곧 원문 유출이다.
                continue
            updated.append({**item, "text": replacement})
            injected = True
        else:
            updated.append(item)
    # 텍스트 파트가 없던 배열(이미지만 있는 메시지)은 검사한 글자도 없다 — 그대로 둔다.
    # ★ 초판은 `replacement`("")를 돌려줘서, 다른 메시지가 마스킹되면 이미지가 지워졌다.
    return updated if injected else content


def segments_from_messages(messages: list[dict[str, Any]]) -> list[Segment]:
    """`messages` 배열을 검사 단위로 옮긴다. 순서와 개수는 1:1 로 보존한다.

    JSON 으로 된 본문(도구 결과)도 원문 그대로 검사한다 — `\\uXXXX` 이스케이프는 정규화가 푼다
    (`garim/normalize/chain.py`). ★ 재검토: 본문을 파싱해 값 단위로 검사하던 판은 소수 표기 숫자를
    놓쳤고, 필드 검사의 크기 예산을 `tools` 와 나눠 써서 큰 에이전트 요청을 막았다.
    """
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


# 검사하지 않는 요청 최상위 키 — 값이 숫자 · 불리언 · 열거값 · 모델 이름뿐인 것만 둔다. 모르는
# 키는 검사한다(fail-closed): 새 필드가 생겨도 검사 없이 나가는 자리가 생기지 않는다.
_UNSCANNED_REQUEST_KEYS = frozenset(
    {
        "messages",  # 아래에서 메시지 단위로 따로 다룬다
        "model",
        "stream",
        "stream_options",
        "n",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "seed",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning_effort",
        "service_tier",
        "store",
        "modalities",
    }
)
# 내용 파트의 이진 본문 — 이미지 · 오디오 · 문서 파싱은 범위 밖이다(§1.3). **이 자리에서만**,
# 그리고 값 전체가 base64 일 때만 건너뛴다. ★ 3차 검토: 어느 깊이에서든 `data:` 로 시작하는
# 문자열과 `file_data` 키를 건너뛰었더니 도구 인자 `{"note": "data:;base64, 주민번호 …"}` 가
# 그대로 나갔다.
#   ★ 전수 검토(2026-10-28) — 글자 집합만 봐서 공백 섞인 영숫자 평문(`card 4111 1111 …`)도
#   "base64" 였고, `data:text/plain;base64,…` 처럼 텍스트 파일도 이진으로 건너뛰었다. 이제 base64
#   로 풀어(줄바꿈 · URL-safe 허용) **텍스트로 읽히면** 푼 글을 검사하고, 가렸으면 다시 인코딩한다.
#   텍스트가 아니면 이진으로 건너뛴다. 짧은 값은 원문도 검사한다 — base64 글자만으로 된 평문
#   (`4111111111111111`)이 이진처럼 보일 수 있다. ★ 재검토: 파일 서명 목록으로 가르던 판은 `BM`
#   으로 시작하는 텍스트를 건너뛰었고, 서명이 목록에 없는 큰 첨부(HWP · 줄바꿈 base64)를 막았다.
_BINARY_PARTS = (("image_url", "url"), ("input_audio", "data"), ("file", "file_data"))
_DATA_URL = re.compile(r"(?P<prefix>data:(?P<mime>[^,]*?)(?:;[^,;]*)*?;base64,)(?P<body>.*)", re.S)
_SHORT_PAYLOAD = 256


@dataclass(slots=True)
class _EncodedText:
    """base64 로 감싼 텍스트 첨부 — 검사하는 동안 푼 글이 문자열 자리를 대신한다."""

    original: str
    prefix: str
    text: str
    encoding: str
    urlsafe: bool
    changed: bool = False

    def render(self) -> str:
        if not self.changed:
            return self.original
        raw = self.text.encode(self.encoding)
        body = base64.urlsafe_b64encode(raw) if self.urlsafe else base64.b64encode(raw)
        return self.prefix + body.decode("ascii")


def _decoded_text(raw: bytes) -> tuple[str, str] | None:
    """텍스트로 읽히는가 — UTF-8 · BOM 붙은 UTF-16 · CP949, NUL 없이 거의 다 인쇄 가능한 글자."""
    has_bom = raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    candidates = ("utf-16",) if has_bom else ("utf-8", "cp949")
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text)
        if text and "\x00" not in text and printable >= 0.95 * len(text):
            return text, encoding
    return None


def _classify_payload(value: str, *, require_data_url: bool) -> str | _EncodedText | Extraction:
    """`"plain"`(문자열로 검사) · `"binary"`(글을 꺼내지 못한 이진) · `_EncodedText`(푼 글을 검사해
    가린다) · `Extraction`(문서에서 꺼낸 글 — 탐지에만 쓴다).

    문서를 텍스트보다 먼저 본다 — ASCII 로만 된 PDF 를 텍스트로 보고 다시 인코딩하면 파일이 깨진다.
    """
    match = _DATA_URL.fullmatch(value)
    if match is not None:
        prefix, body = match.group("prefix"), match.group("body")
    elif require_data_url:
        return "plain"  # `image_url.url` 의 http(s) 주소
    else:
        prefix, body = "", value
    compact = re.sub(r"\s+", "", body)
    urlsafe = "-" in compact or "_" in compact
    try:
        raw = base64.b64decode(
            compact + "=" * (-len(compact) % 4),
            altchars=b"-_" if urlsafe else None,
            validate=True,
        )
    except ValueError:
        return "plain"
    if not raw:
        return "plain"
    document = extract_document(raw)
    if document is not None:
        return document
    decoded = _decoded_text(raw)
    if decoded is not None:
        return _EncodedText(value, prefix, decoded[0], decoded[1], urlsafe)
    return "binary"


# 메시지의 프로토콜 키 — 이 키 이름은 검사하지 않는다.
_HANDLED_MESSAGE_KEYS = frozenset(
    {"role", "content", "tool_calls", "function_call", "tool_call_id"}
)
# 대화를 잇는 ID 는 이 모양의 문자열일 때만 건너뛴다 — dict · 긴 문자열이면 검사한다(전수 검토).
# ★ 재검토 — `\w` 가 유니코드라 `홍길동` · `010-1234-5678` 도 ID 모양이었다. ASCII 로 묶고,
# 숫자 · 구분자로만 된 값(번호)은 ID 가 아니다.
_PROTOCOL_ID = re.compile(r"(?=.*[A-Za-z_])[A-Za-z0-9_\-:.]{1,128}", re.ASCII)
_SETTING_NAME = re.compile(r"(?=.*[A-Za-z_])[A-Za-z0-9_\-:./]{1,128}", re.ASCII)
_STREAM_OPTIONS = frozenset({"include_usage", "include_obfuscation"})


def _expected_setting(key: str, value: Any) -> bool:
    """검사하지 않는 최상위 설정 키가 정말 설정 모양인가. 아니면 문자열 리프를 검사한다."""
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return bool(_SETTING_NAME.fullmatch(value))
    if key == "stream_options" and isinstance(value, dict):
        return all(
            name in _STREAM_OPTIONS and isinstance(item, bool) for name, item in value.items()
        )
    if key == "logit_bias" and isinstance(value, dict):
        return all(
            str(name).isdigit() and isinstance(item, int | float) for name, item in value.items()
        )
    if key == "modalities" and isinstance(value, list):
        return all(isinstance(item, str) and _SETTING_NAME.fullmatch(item) for item in value)
    if key == "tool_choice" and isinstance(value, dict):

        def names(node: Any) -> bool:
            if isinstance(node, dict):
                return all(_SETTING_NAME.fullmatch(str(k)) and names(v) for k, v in node.items())
            if isinstance(node, list):
                return all(names(item) for item in node)
            return not isinstance(node, str) or bool(_SETTING_NAME.fullmatch(node))

        return names(value)
    return False


# 필드 값을 한 세그먼트로 이을 때의 경계. 규칙이 잡는 문자 집합 밖이라 스팬이 넘지 않는다.
_FIELD_SEPARATOR = "\n\ue000\n"


@dataclass(slots=True)
class _Arguments:
    """JSON 으로 읽힌 도구 인자 — 검사하는 동안 문자열 자리를 대신한다."""

    original: str
    value: Any
    changed: bool = False


class _RepeatedKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """★ 3차 검토: `{"to": 원문, "to": "x"}` 는 `json.loads` 가 뒤 값만 남겨 앞 값을 검사하지
    않았고, 바뀐 것이 없으니 원래 바이트가 그대로 나갔다. 검사한 것과 보내는 것이 갈린다."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _RepeatedKeyError
    return dict(pairs)


@dataclass(frozen=True, slots=True)
class _KeyName:
    """경로의 마지막 칸 — 값이 아니라 그 dict 의 키 이름을 가리킨다."""

    name: str


@dataclass(frozen=True, slots=True)
class _Leaf:
    """본문 밖에서 상류로 가는 문자열 하나와 그 위치."""

    path: tuple[Any, ...]
    text: str
    origin: Origin


def _parse_arguments(index: int, arguments: Any) -> Any:
    if not isinstance(arguments, str):
        # OpenAI 계약은 JSON 문자열이다. 다른 모양을 추측해 직렬화하면 상류가 받는
        # 바이트와 우리가 검사한 바이트가 갈린다.
        raise ProxyError(400, f"messages[{index}] tool call arguments must be a JSON string")
    try:
        parsed = json.loads(arguments, object_pairs_hook=_unique_object)
    except _RepeatedKeyError as exc:
        raise ProxyError(
            400, f"messages[{index}] tool call arguments repeat a key; request was not forwarded"
        ) from exc
    except json.JSONDecodeError:
        return arguments  # JSON 이 아니면 문자열 하나로 검사한다
    # `\uXXXX` 이스케이프를 푼 값을 검사한다 — 기본 `json.dumps` 는 한글을 전부 이스케이프하고
    # 규칙은 이스케이프된 바이트를 읽지 못한다. JSON 문자열 하나(`"…\nkim@…"`)도 푼 값으로
    # 검사해야 마스킹이 이스케이프를 먹지 않는다(3차 검토).
    return _Arguments(arguments, parsed)


def _working_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """상류로 보낼 사본. 도구 인자는 검사하는 동안 파싱된 값으로 둔다."""
    working = copy.deepcopy(payload)
    for index, message in enumerate(working["messages"]):
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ProxyError(400, f"messages[{index}].tool_calls must be an array")
        functions = [call.get("function") for call in calls or [] if isinstance(call, dict)]
        functions.append(message.get("function_call"))  # 구식 `function_call`
        for function in functions:
            if isinstance(function, dict) and "arguments" in function:
                function["arguments"] = _parse_arguments(index, function["arguments"])
    return working


@dataclass(frozen=True, slots=True)
class _Attachment:
    """가리지 못하는 첨부 — 문서(꺼낸 글이 `text`)이거나 글을 꺼내지 못한 것(`text` 가 None)."""

    message: int
    origin: Origin
    kind: str
    text: str | None
    reason: str | None = None


def _prepare_part(part: Any, attachments: list[_Attachment], message: int, origin: Origin) -> Any:
    """내용 파트의 첨부를 검사할 꼴로 바꾼다. 경로는 원래 파트와 같다.

    - 텍스트 첨부는 제자리에서 `_EncodedText` 가 되어 다른 값처럼 가린다.
    - 문서는 꺼낸 글을 `attachments` 에 담고, 보기에서 본문을 뺀다 — 탐지만 하고 가리지 않는다.
    - 글을 꺼내지 못한 이진은 `attachments` 에 담는다. 짧은 값은 보기에 남겨 원문도 검사한다
      (base64 글자만으로 된 평문이 이진처럼 보일 수 있다). 원격 이미지 주소도 내용은 못 본다.
    """
    if not isinstance(part, dict):
        return part
    view = dict(part)
    for outer, inner in _BINARY_PARTS:
        holder = part.get(outer)
        value = holder.get(inner) if isinstance(holder, dict) else None
        if not isinstance(value, str):
            continue
        kind = _classify_payload(value, require_data_url=inner == "url")
        if isinstance(kind, _EncodedText):
            holder[inner] = kind
        elif isinstance(kind, Extraction):
            attachments.append(_Attachment(message, origin, kind.kind, kind.text, kind.reason))
            view[outer] = {k: v for k, v in holder.items() if k != inner}
        elif kind == "binary":
            attachments.append(_Attachment(message, origin, outer, None, "binary"))
            if len(value) > _SHORT_PAYLOAD:
                view[outer] = {k: v for k, v in holder.items() if k != inner}
        elif outer == "image_url" and value.startswith(("http://", "https://")):
            attachments.append(_Attachment(message, origin, "image_url", None, "remote"))
    return view


def _collect(node: Any, path: tuple[Any, ...], origin: Origin, out: list[_Leaf]) -> None:
    if isinstance(node, _Arguments):
        _collect(node.value, path, origin, out)
    elif isinstance(node, str):
        if node:
            out.append(_Leaf(path, node, origin))
    elif isinstance(node, _EncodedText):
        if node.text:
            out.append(_Leaf(path, node.text, origin))
    elif isinstance(node, dict):
        for key, value in node.items():
            # 키도 상류로 가는 문자열이다 — `{"contacts": {"010-…": "김철수"}}` 의 번호는
            # 키 자리에 있다.
            if isinstance(key, str) and key:
                out.append(_Leaf((*path, _KeyName(key)), key, origin))
            _collect(value, (*path, key), origin, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect(value, (*path, index), origin, out)


def _field_leaves(
    working: dict[str, Any], attachments: list[_Attachment] | None = None
) -> list[_Leaf]:
    """본문(`content` 의 텍스트) 밖에서 상류로 가는 문자열을 전부 모은다.

    ★ **원문 유출이 있었다.** 초판은 `content` 만 읽었고, 다음에 도구 인자를 더했지만
      나머지는 그대로 보냈다 — `prediction.content` · `tools[].function.description` ·
      메시지 `name` · `refusal` 파트 · custom 도구 입력 · 파일 이름 · URL 쿼리의 이메일이
      원문으로 나갔다. 그래서 목록을 늘리는 대신 **빼는 것만** 적는다: 모델 설정 키,
      대화를 잇는 무작위 ID(`tool_call_id` · `tool_calls[].id`), 내용 파트의 base64 본문.

    값은 JSON 텍스트가 아니라 **문자열 그대로** 검사한다. 직렬화한 채 검사하면 `\\n` 의
    `n` 이 이메일 앞에 붙어 마스킹이 이스케이프를 반쯤 먹었다. 값 안의 dict 키도 검사한다
    (요청 최상위 · 메시지의 프로토콜 키는 빼고).
    """
    leaves: list[_Leaf] = []
    for key, value in working.items():
        if key == "messages":
            continue
        if key not in _UNSCANNED_REQUEST_KEYS or not _expected_setting(key, value):
            # 요청 최상위 필드는 개발자가 채우는 설정이다. 모르는 키는 이름도 검사한다.
            leaves.append(_Leaf((_KeyName(key),), key, "system"))
            _collect(value, (key,), "system", leaves)
    for index, message in enumerate(working["messages"]):
        origin = ROLE_TO_ORIGIN[str(message.get("role", ""))]
        for key, value in message.items():
            path: tuple[Any, ...] = ("messages", index, key)
            if key not in _HANDLED_MESSAGE_KEYS:
                leaves.append(_Leaf(("messages", index, _KeyName(key)), key, origin))
            if key == "role" or (key == "content" and isinstance(value, str)):
                continue  # 본문 문자열은 메시지 세그먼트가 검사한다
            if key == "tool_call_id" and isinstance(value, str) and _PROTOCOL_ID.fullmatch(value):
                continue
            if key == "content" and isinstance(value, list):
                for part_index, part in enumerate(value):
                    if _is_text_part(part):
                        part = {k: v for k, v in part.items() if k != "text"}
                    prepared = _prepare_part(
                        part, attachments if attachments is not None else [], index, origin
                    )
                    _collect(prepared, (*path, part_index), origin, leaves)
            elif key == "tool_calls" and isinstance(value, list):
                for call_index, call in enumerate(value):
                    call_id = call.get("id") if isinstance(call, dict) else None
                    if isinstance(call_id, str) and _PROTOCOL_ID.fullmatch(call_id):
                        call = {k: v for k, v in call.items() if k != "id"}
                    _collect(call, (*path, call_index), origin, leaves)
            else:
                _collect(value, path, origin, leaves)
    return leaves


def _assign(root: Any, path: tuple[Any, ...], value: str) -> None:
    """가린 값을 제자리에 쓴다. 키 이름이 가려졌으면 순서를 지킨 채 키를 바꾼다.

    키를 바꾸면 그 아래 경로가 옛 이름을 가리키므로, 호출부는 모은 순서의 **역순**으로 쓴다.
    """
    node, owner = root, None
    for key in path[:-1]:
        node = node[key]
        if isinstance(node, _Arguments):
            owner, node = node, node.value
    if isinstance(path[-1], _KeyName):
        old = path[-1].name
        if value in node:
            raise ProxyError(400, "masking would merge two request keys; request was not forwarded")
        items = list(node.items())
        node.clear()
        node.update((value if key == old else key, item) for key, item in items)
        if owner is not None:
            owner.changed = True
        return
    target = node[path[-1]]
    if isinstance(target, _Arguments):  # 인자 전체가 JSON 문자열 하나였다
        target.value, target.changed = value, True
        return
    if isinstance(target, _EncodedText):  # 텍스트 첨부 — 보낼 때 다시 인코딩한다
        target.text, target.changed = value, True
        return
    node[path[-1]] = value
    if owner is not None:
        owner.changed = True


def _restore_argument_strings(working: dict[str, Any]) -> None:
    """파싱해 둔 인자 · 텍스트 첨부를 문자열로 되돌린다. 가린 것이 없으면 원래 바이트 그대로다."""
    for message in working["messages"]:
        content = message.get("content")
        for part in content if isinstance(content, list) else []:
            for outer, inner in _BINARY_PARTS:
                holder = part.get(outer) if isinstance(part, dict) else None
                if isinstance(holder, dict) and isinstance(holder.get(inner), _EncodedText):
                    holder[inner] = holder[inner].render()
        calls = message.get("tool_calls") or []
        functions = [call.get("function") for call in calls if isinstance(call, dict)]
        functions.append(message.get("function_call"))
        for function in functions:
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if isinstance(arguments, _Arguments):
                function["arguments"] = (
                    json.dumps(arguments.value, ensure_ascii=False)
                    if arguments.changed
                    else arguments.original
                )


def _field_groups(leaves: list[_Leaf]) -> list[tuple[Origin, list[_Leaf]]]:
    """origin 별로 묶어 세그먼트 몇 개로 만든다 — 값마다 세그먼트를 만들면 상한을 넘는다."""
    groups: dict[Origin, list[_Leaf]] = {}
    for leaf in leaves:
        if "\ue000" in leaf.text:
            raise ProxyError(400, "request contains U+E000, which the proxy reserves")
        groups.setdefault(leaf.origin, []).append(leaf)
    return list(groups.items())


def _leaf_message(group: list[_Leaf], offset: int) -> int | None:
    """필드 세그먼트 안의 오프셋 → 그 값이 속한 메시지 번호(최상위 필드면 None)."""
    start = 0
    for leaf in group:
        end = start + len(leaf.text)
        if offset < end + len(_FIELD_SEPARATOR):
            return leaf.path[1] if leaf.path[0] == "messages" else None  # type: ignore[return-value]
        start = end + len(_FIELD_SEPARATOR)
    return None


def _blocked_body(
    results: list[tuple[CheckResult, Any]], verdict: str, extra_reasons: set[str] = frozenset()
) -> dict[str, Any]:
    """`results` 는 (검사 결과, 세그먼트 · 오프셋 → (메시지 세그먼트 번호, 메시지 번호)) 쌍이다.

    필드 묶음의 세그먼트 번호는 메시지 번호와 겹치므로 내보내지 않는다(null).
    """
    return {
        "error": {
            "type": "garim_blocked",
            "message": "가림이 이 요청을 차단했습니다. 상류로 전달하지 않았습니다.",
            "verdict": verdict,
            "degraded": any(result.degraded for result, _ in results),
            "degraded_reasons": sorted(
                {reason for result, _ in results for reason in result.degraded_reasons}
                | set(extra_reasons)
            ),
            "findings": [
                {
                    "code": finding.code,
                    "segment": segment,
                    "message": message,
                    "rule": finding.rule,
                }
                for result, locate in results
                for finding in (*result.leaks, *result.attacks)
                for segment, message in [locate(finding.segment_idx, finding.span[0])]
            ],
        }
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class ProxyService:
    """검사 → (허용 시) 상류 전달 → 복원. 전송은 `sender` 로 주입해 테스트한다."""

    def __init__(
        self,
        config: ProxyConfig,
        *,
        sender: Any = None,
        l1_backend: L1Backend | None = None,
        l2_backend: L2Backend | None = None,
    ) -> None:
        self.config = config
        # 모델은 프로세스에 한 번만 올린다. 볼트는 요청마다 새로 만들어야 하므로
        # (다른 요청의 원문이 이 응답에 복원되면 안 된다) Inspector 는 요청마다,
        # 백엔드는 공유다. 공유를 견디는 조건은 `OnnxL1Backend` 가 진다.
        # ★ 전수 검토 — `inspector=` 인자로 Inspector 하나를 넘기면 모든 요청이 볼트 하나를 나눠
        #   써서 앞 요청의 번호가 뒤 요청 응답에 복원됐다. 그 인자를 없앴다.
        if l1_backend is None and config.l1_model_dir is not None:
            from garim.detect.l1 import OnnxL1Backend

            l1_backend = OnnxL1Backend(config.l1_model_dir, model_filename=config.l1_model_file)

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
        # ★ 전수 검토 — 기본 opener 는 3xx 를 따라가며 사용자의 Authorization 을 다른 호스트
        #   (https → http 포함)로 그대로 보냈다. 리다이렉트는 따라가지 않는다.
        opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPSHandler(context=https_context())
        )
        try:
            with opener.open(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if not 400 <= exc.code < 600:
                raise ProxyError(
                    502, f"upstream answered {exc.code}; redirects are not followed"
                ) from exc
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
        attachments: list[_Attachment] = []
        try:
            working = _working_copy(payload)
            groups = _field_groups(_field_leaves(working, attachments))
        except RecursionError as exc:
            raise ProxyError(400, "request is nested too deeply") from exc
        inspector = self._inspector_factory()
        # 두 검사가 볼트를 나눠 쓴다 — 본문 · 필드에 있는 `<PHONE_1>` 같은 글자를 어느 검사보다
        # 먼저 피한다. ★ 전수 검토: 필드 쪽만 미리 피해서, 본문의 `<PHONE_1>` 과 필드 번호의
        # 토큰이 같아졌다.
        for segment in segments:
            inspector.vault.reserve_literals(segment.text)
        for _, group in groups:
            for leaf in group:
                inspector.vault.reserve_literals(leaf.text)
        result = inspector.check(segments, mode=self.config.mode)
        # 본문 밖 필드는 따로 검사한다 — 큰 `tools` 정의가 본문과 `max_input_chars` 를 나눠
        # 쓰면 PII 하나 없는 에이전트 요청이 `input_truncated` 로 차단됐다. 같은 Inspector 라
        # 볼트를 공유하고 토큰 번호가 겹치지 않는다.
        fields = (
            inspector.check(
                [
                    Segment(_FIELD_SEPARATOR.join(leaf.text for leaf in group), origin=origin)
                    for origin, group in groups
                ],
                mode=self.config.mode,
            )
            if groups
            else None
        )
        checked = [(result, lambda segment, offset: (segment, segment))]
        if fields is not None:
            checked.append(
                (fields, lambda segment, offset: (None, _leaf_message(groups[segment][1], offset)))
            )
        verdicts = [item.verdict for item, _ in checked]
        extra_reasons: set[str] = set()
        # 첨부 — 문서는 꺼낸 글을 따로 검사하고(가릴 수는 없다), 글을 못 꺼낸 첨부는 정책을 따른다.
        attachment_policy = inspector.policy.config.attachments
        documents = [item for item in attachments if item.text]
        if documents:
            documents_result = inspector.check(
                [Segment(item.text or "", origin=item.origin) for item in documents],
                mode=self.config.mode,
            )
            checked.append(
                (
                    documents_result,
                    lambda segment, offset: (None, documents[segment].message),
                )
            )
            document_verdict = documents_result.verdict
            if document_verdict == "mask":
                document_verdict = attachment_policy.unmaskable
                extra_reasons.add("attachment_unmaskable")
            verdicts.append(document_verdict)
        uninspected = [item for item in attachments if item.text is None]
        if uninspected and attachment_policy.uninspected != "allow":
            verdicts.append(attachment_policy.uninspected)
            extra_reasons.add("attachment_not_inspected")
        verdict = max(verdicts, key=SEVERITY.__getitem__)
        degraded = any(item.degraded for item, _ in checked)
        reasons = sorted(
            {reason for item, _ in checked for reason in item.degraded_reasons} | extra_reasons
        )
        response_headers = {
            "X-Garim-Verdict": verdict,
            "X-Garim-Degraded": "true" if degraded else "false",
        }
        if reasons:
            response_headers["X-Garim-Degraded-Reasons"] = ",".join(reasons)

        if verdict == "block":
            return 400, _blocked_body(checked, verdict, extra_reasons), response_headers

        outbound = working
        if fields is not None and fields.masked_segments is not None:
            for (_, group), text in zip(groups, fields.masked_segments, strict=True):
                pieces = text.split(_FIELD_SEPARATOR)
                if len(pieces) != len(group):
                    raise ProxyError(
                        400, "masking crossed a field boundary; request was not forwarded"
                    )
                # 역순 — 키 이름을 바꾸기 전에 그 아래 값부터 쓴다(`_assign`).
                for leaf, piece in reversed(list(zip(group, pieces, strict=True))):
                    if piece != leaf.text:
                        _assign(outbound, leaf.path, piece)
        _restore_argument_strings(outbound)
        if result.masked_segments is not None:
            outbound["messages"] = [
                {**message, "content": _with_text(message.get("content"), text)}
                for message, text in zip(outbound["messages"], result.masked_segments, strict=True)
            ]
        masked = result.masked_segments is not None or (
            fields is not None and fields.masked_segments is not None
        )

        if stream:
            # 상류에는 스트리밍 없이 묻는다 — 전문을 복원한 뒤 SSE 로 다시 쪼갠다(`to_sse`).
            outbound["stream"] = False
            outbound.pop("stream_options", None)
        upstream = self._sender("/chat/completions", outbound, headers)
        if stream and not _completion_shaped(upstream):
            # SSE 로 다시 쪼갤 수 없다 — 연결을 끊는 대신 502 로 알린다(전수 검토).
            raise ProxyError(502, "upstream returned a body that is not a chat completion")
        if masked:
            upstream, restore_missing = _restore_choices(upstream, inspector.vault)
            if restore_missing:
                response_headers["X-Garim-Verdict"] = "warn"
                response_headers["X-Garim-Degraded"] = "true"
                existing = response_headers.get("X-Garim-Degraded-Reasons")
                reasons = [reason for reason in (existing or "").split(",") if reason]
                if "vault_restore_miss" not in reasons:
                    reasons.append("vault_restore_miss")
                response_headers["X-Garim-Degraded-Reasons"] = ",".join(reasons)
        return 200, upstream, response_headers


def _completion_shaped(response: Any) -> bool:
    choices = response.get("choices") if isinstance(response, dict) else None
    return isinstance(choices, list) and all(isinstance(choice, dict) for choice in choices)


def _restore_text_fields(message: dict[str, Any], vault: Any) -> bool:
    """`content` · `refusal` · `reasoning_content`(문자열 또는 `text` 파트 배열)을 복원한다.

    ★ 전수 검토 — `content` 문자열만 복원해서 `refusal` · 추론 본문 · 파트 배열의 토큰이 남았고,
      없는 토큰으로도 보고되지 않았다.
    """
    missing = False
    for key in ("content", "refusal", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str):
            message[key], key_missing = vault.restore_with_status(value)
            missing = missing or key_missing
        elif isinstance(value, list):
            parts = []
            for part in value:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text, part_missing = vault.restore_with_status(part["text"])
                    missing = missing or part_missing
                    part = {**part, "text": text}
                parts.append(part)
            message[key] = parts
    return missing


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
        missing = _restore_text_fields(message, vault) or missing
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
        events.append(
            {
                "index": index,
                "delta": {"role": message.get("role", "assistant")},
                "finish_reason": None,
            }
        )
        delta: dict[str, Any] = {}
        for key in ("content", "refusal", "reasoning_content"):
            if isinstance(message.get(key), str):
                delta[key] = message[key]
        if isinstance(message.get("tool_calls"), list):
            delta["tool_calls"] = [
                {"index": call_index, **call}
                for call_index, call in enumerate(message["tool_calls"])
            ]
        if isinstance(message.get("function_call"), dict):
            delta["function_call"] = message["function_call"]
        if delta:
            events.append({"index": index, "delta": delta, "finish_reason": None})
        events.append(
            {"index": index, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}
        )
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
    # 본문을 보내지 않고 붙잡고 있는 연결이 스레드를 계속 묶어 두지 못하게 한다. 상류를
    # 기다리는 동안에는 이 소켓을 읽지 않으므로 `--upstream-timeout` 과는 무관하다.
    timeout = 60

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """★ 프롬프트 원문이 접근 로그로 새지 않게 기본 로깅을 끈다(§3.2)."""

    def _send_headers(self, headers: dict[str, str]) -> None:
        # ★ 전수 검토 — 사유에 한글 예외 이름(`l1_error:모델오류`)이 들어가면 `send_header` 가
        #   UnicodeEncodeError 로 죽어, 상류에 이미 보낸 요청의 응답이 사라졌다.
        for key, value in headers.items():
            self.send_header(key, value.encode("ascii", "backslashreplace").decode("ascii"))

    def _reply_sse(self, body: str, headers: dict[str, str]) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self._send_headers(headers)
        self.end_headers()
        self.wfile.write(encoded)

    def _reply(self, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self._send_headers(headers)
        self.end_headers()
        self.wfile.write(encoded)

    def _refuse_unread(self, status: int, message: str) -> None:
        """본문을 읽지 않고 거절한다 — 연결을 닫는다.

        ★ keep-alive 로 두면 읽지 않은 본문 바이트가 같은 연결의 다음 요청으로 해석됐다.
        """
        self.close_connection = True
        self._reply(status, {"error": {"message": message}}, {})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._refuse_unread(404, "garim proxy serves POST /v1/chat/completions only")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self._refuse_unread(404, "garim proxy serves POST /v1/chat/completions only")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._refuse_unread(400, "invalid Content-Length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._refuse_unread(413, "request body is missing or too large")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            # UnicodeDecodeError · JSONDecodeError · 4,300자리 넘는 정수(ValueError) · 깊은 중첩.
            # ★ 전수 검토 — 뒤의 둘은 응답 없이 연결이 끊겼다.
            self._reply(400, {"error": {"message": "body is not valid JSON"}}, {})
            return
        if not isinstance(payload, dict):
            self._reply(400, {"error": {"message": "body must be a JSON object"}}, {})
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
            try:
                rendered = to_sse(body, include_usage=include_usage)
            except Exception as exc:  # 끊지 말고 알린다
                self._reply(502, {"error": {"message": f"cannot stream: {type(exc).__name__}"}}, {})
                return
            self._reply_sse(rendered, headers)
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
    """기본값은 `127.0.0.1` 이다 — 다른 `host` 를 넘기면 그 주소에 그대로 바인드한다."""
    service = ProxyService(config, l1_backend=l1_backend, l2_backend=l2_backend)
    handler = type("GarimProxyHandler", (_Handler,), {"service": service})
    # ★ 전수 검토 — `::1` 을 루프백으로 받으면서 IPv4 전용 서버로 열어 시작하지 못했다.
    server_class = _IPv6Server if ":" in host else ThreadingHTTPServer
    return server_class((host, port), handler)


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6
