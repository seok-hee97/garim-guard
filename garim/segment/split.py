"""Lossless automatic splitting for mixed prose/code inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from garim.models import ContentType
from garim.segment.content_type import detect_content_type

_FENCE = re.compile(r"^\s*```|^\s*~~~")
_INDENTED = re.compile(r"^(?: {4}|\t)\S")


@dataclass(frozen=True, slots=True)
class SegmentSlice:
    text: str
    start: int
    end: int
    content_type: ContentType


def split_text(text: str) -> list[SegmentSlice]:
    """Split fenced/indented code while preserving every source character exactly."""
    if not text:
        return [SegmentSlice("", 0, 0, "prose")]
    whole_type = detect_content_type(text)
    if whole_type in {"structured", "log"}:
        return [SegmentSlice(text, 0, len(text), whole_type)]

    typed_lines: list[tuple[int, int, ContentType]] = []
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fence = bool(_FENCE.match(stripped))
        line_type: ContentType = "code" if in_fence or fence or _INDENTED.match(line) else "prose"
        typed_lines.append((offset, offset + len(line), line_type))
        if fence:
            in_fence = not in_fence
        offset += len(line)
    if offset < len(text):
        typed_lines.append((offset, len(text), "code" if in_fence else "prose"))

    merged: list[SegmentSlice] = []
    for start, end, content_type in typed_lines:
        if merged and merged[-1].content_type == content_type and merged[-1].end == start:
            previous = merged.pop()
            merged.append(
                SegmentSlice(text[previous.start : end], previous.start, end, content_type)
            )
        else:
            merged.append(SegmentSlice(text[start:end], start, end, content_type))
    return merged or [SegmentSlice(text, 0, len(text), whole_type)]
