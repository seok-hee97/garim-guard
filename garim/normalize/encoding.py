"""Conservative, one-level base64/hex decoded views."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from garim.normalize.offsets import IntervalMap

_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{16,}(?![A-Za-z0-9+/=_-])")
_HEX = re.compile(r"[0-9A-Fa-f]+\Z")


@dataclass(frozen=True, slots=True)
class DecodedView:
    text: str
    interval_map: IntervalMap
    source_span: tuple[int, int]
    encoding: str


def _printable_ratio(value: str) -> float:
    if not value:
        return 0.0
    return sum(character.isprintable() or character in "\r\n\t" for character in value) / len(value)


def decoded_views(text: str, interval_map: IntervalMap) -> list[DecodedView]:
    views: list[DecodedView] = []
    occupied: set[tuple[int, int]] = set()
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        decoded: bytes | None = None
        encoding = ""
        if len(token) >= 16 and len(token) % 2 == 0 and _HEX.fullmatch(token):
            try:
                decoded = bytes.fromhex(token)
                encoding = "hex"
            except ValueError:
                decoded = None
        # URL-safe base64 commonly omits trailing ``=`` padding.  A payload whose
        # length is 2 or 3 modulo 4 is still decodable after restoring that padding;
        # only modulo 1 is structurally impossible.  Requiring padding to be present
        # opened a trivial bypass for every encoded detector input.
        if decoded is None and len(token) % 4 != 1:
            try:
                padding = "=" * (-len(token) % 4)
                decoded = base64.b64decode(token + padding, altchars=b"-_", validate=True)
                encoding = "base64"
            except (ValueError, base64.binascii.Error):
                decoded = None
        if decoded is None:
            continue
        try:
            rendered = decoded.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _printable_ratio(rendered) < 0.9:
            continue
        source_intervals = interval_map[match.start() : match.end()]
        if not source_intervals:
            continue
        span = (
            min(start for start, _ in source_intervals),
            max(end for _, end in source_intervals),
        )
        if span in occupied:
            continue
        occupied.add(span)
        views.append(DecodedView(rendered, [span] * len(rendered), span, encoding))
    return views
