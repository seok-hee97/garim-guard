"""Fast, deterministic content-type detection."""

from __future__ import annotations

import json
import re

from garim.models import ContentType

_LOG_LINE = re.compile(
    r"(?m)^(?:\[?(?:19|20)\d{2}[-/.]\d{2}[-/.]\d{2}|"
    r"(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b|Traceback \(most recent call last\))"
)
_CODE_SIGNAL = re.compile(
    r"(?m)(?:^\s*(?:def|class|func|function|import|from|const|let|var|SELECT|INSERT)\b|"
    r"[{};]\s*$|=>|</?[A-Za-z][^>]*>)"
)
_STRUCTURED_LINE = re.compile(r"(?m)^\s*(?:[-\w가-힣]+\s*:\s*\S|[\[{]\s*$)")


def _is_json(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
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
