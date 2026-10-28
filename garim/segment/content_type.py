"""Fast, deterministic content-type detection."""

from __future__ import annotations

import json
import re

from garim.models import ContentType

_LOG_LINE = re.compile(
    r"(?m)^(?:\[?(?:19|20)\d{2}[-/.]\d{2}[-/.]\d{2}|"
    r"(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b|Traceback \(most recent call last\))"
)
# ★ 줄 안의 공백만 본다(`[ \t]`). 여러 줄 모드의 `^\s*` · `\s*$` 는 빈 줄을 건너 다음 줄까지
#   먹어서, 빈 줄 1만 개(`"x" + "\n" * 10000 + "x"`)에 15초가 걸렸다(5차 검토).
_CODE_SIGNAL = re.compile(
    r"(?m)(?:^[ \t]*(?:def|class|func|function|import|from|const|let|var|SELECT|INSERT)\b|"
    r"[{};][ \t]*$|=>|</?[A-Za-z][^<>\n]*>)"
)
_STRUCTURED_LINE = re.compile(
    r"(?m)^[ \t]*(?:[-\w가-힣]+[ \t]*:(?:[ \t]*\S|[ \t]*$)|[\[{][ \t]*$)"
)


def _is_json(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        # ★ 4,300자리를 넘는 정수는 `json.loads` 가 `ValueError` 를 낸다(JSONDecodeError 아님).
        #   잡지 않으면 긴 숫자 하나가 검사 전체를 멈췄다(5차 검토).
        return False
    return isinstance(parsed, dict | list)


def detect_content_type(text: str) -> ContentType:
    """Classify a segment; uncertain input deliberately falls back to prose."""
    stripped = text.strip()
    if not stripped:
        return "prose"
    if _is_json(stripped):
        return "structured"
    lines = stripped.splitlines()
    log_hits = len(_LOG_LINE.findall(stripped))
    if log_hits >= max(1, len(lines) // 3):
        return "log"
    code_hits = len(_CODE_SIGNAL.findall(stripped))
    if stripped.startswith("```") or code_hits >= max(1, len(lines) // 3):
        return "code"
    structured_hits = len(_STRUCTURED_LINE.findall(stripped))
    if len(lines) >= 2 and structured_hits >= max(2, len(lines) // 2):
        return "structured"
    return "prose"
