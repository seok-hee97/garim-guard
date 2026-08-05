"""Korean confidentiality marking recognizer with position-aware confidence."""

from __future__ import annotations

import re

from garim.models import Finding

_MARKER = re.compile(
    r"대외비|사내한|내부한정|외부유출금지|무단배포금지|보안등급\s*[:：]?\s*[1-4]급|"
    r"CONFIDENTIAL|INTERNAL\s+ONLY|DO\s+NOT\s+DISTRIBUTE",
    re.IGNORECASE,
)


def detect_markers(text: str) -> list[Finding]:
    findings: list[Finding] = []
    length = max(1, len(text))
    for match in _MARKER.finditer(text):
        relative = match.start() / length
        near_boundary = relative <= 0.15 or relative >= 0.85
        confidence = 0.85 if near_boundary else 0.65
        rule = "confidential_marker_boundary" if near_boundary else "confidential_marker_body"
        findings.append(Finding("L-DOC-01", match.span(), confidence, "L0", rule))
    return findings
