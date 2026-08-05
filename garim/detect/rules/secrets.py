"""Prompt-oriented secret detection: precise prefixes first, heuristics gated by context."""

from __future__ import annotations

import math
import re
from collections import Counter

from garim.models import Finding

_PREFIX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"), "openai_key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic_key"),
    (re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"), "aws_access_key"),
    (re.compile(r"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"), "github_token"),
    (re.compile(r"(?:xox[baprs]-[A-Za-z0-9-]{20,}|xapp-[A-Za-z0-9-]{20,})"), "slack_token"),
    (re.compile(r"AIza[A-Za-z0-9_-]{30,}"), "google_api_key"),
    (re.compile(r"(?:glpat-|hf_)[A-Za-z0-9_-]{20,}"), "developer_token"),
)
_KOREAN_HEADER = re.compile(
    r"(?i)(?:Authorization\s*:\s*KakaoAK|KAKAO_(?:REST_)?API_KEY|"
    r"NCP_(?:ACCESS_KEY_ID|SECRET_KEY)|TOSS_(?:SECRET|CLIENT)_KEY|DATA_GO_KR_KEY)"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9_+/=-]{12,})"
)
_PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_DB_URL = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/]+:[^\s@]+@[^\s]+"
)
_ENV = re.compile(
    r"(?im)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)"
    r"\s*=\s*([^\s#]{8,})"
)
_GENERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_+/=-]{20,}(?![A-Za-z0-9_-])")
_SECRET_ANCHOR = re.compile(
    r"(?i)(?:api[_ -]?key|secret|token|password|비밀번호|인증키|접근키|시크릿)"
)
_PLACEHOLDER = re.compile(
    r"(?i)(?:example|sample|dummy|placeholder|your[_-]|changeme|test[_-]?key|x{4,})"
)


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def detect_secrets(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern, rule in _PREFIX_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Finding("L-SEC-01", match.span(), 1.0, "L0", rule))
    for match in _KOREAN_HEADER.finditer(text):
        findings.append(Finding("L-SEC-02", match.span(1), 0.95, "L0", "korean_service_header"))
    for match in _PEM.finditer(text):
        findings.append(Finding("L-SEC-03", match.span(), 1.0, "L0", "private_key_header"))
    for match in _JWT.finditer(text):
        findings.append(Finding("L-SEC-03", match.span(), 0.95, "L0", "jwt_format"))
    for match in _DB_URL.finditer(text):
        findings.append(Finding("L-SEC-04", match.span(), 0.95, "L0", "database_url"))
    for match in _ENV.finditer(text):
        findings.append(Finding("L-SEC-04", match.span(2), 0.9, "L0", "secret_assignment"))

    for match in _GENERIC_TOKEN.finditer(text):
        value = match.group(0)
        if _PLACEHOLDER.search(value) or not value.isascii() or _entropy(value) < 3.5:
            continue
        context = text[max(0, match.start() - 40) : match.start()]
        if _SECRET_ANCHOR.search(context):
            findings.append(Finding("L-SEC-05", match.span(), 0.7, "L0", "anchored_entropy"))
    return findings
