"""Common PII recognizers with deterministic validation."""

from __future__ import annotations

import ipaddress
import re

from garim.detect.rules.checksum import luhn_valid
from garim.models import Finding

# ★ 끝 경계 — 초판 `(?![A-Z0-9_.-])` 는 문장 끝 마침표까지 거부해서 `kim@example.com.` 이
#   통째로 탐지되지 않았다(4차 검토). `.` · `_` 는 뒤에 영숫자가 이어질 때만(`x.y_z` 식별자) 막는다.
_EMAIL = re.compile(
    r"(?<![A-Z0-9_.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24}(?![A-Z0-9-])(?![._][A-Z0-9])",
    re.IGNORECASE,
)
# 점 구분(`010.1234.5678`)은 뒤에 `.숫자` 가 이어지면(버전 문자열) 받지 않는다. 그 검사는
# 점 구분에만 건다 — ★ 6차 검토: 모든 구분자에 걸었더니 `010-1234-5678.3시 이후` 의 번호가
# 통째로 나갔다.
_PHONE = re.compile(
    r"(?<!\d)(?:\+82[- .]?1[016789]|01[016789])"
    r"(?:[- ]?\d{3,4}[- ]?\d{4}(?!\d)|\.\d{3,4}\.\d{4}(?!\.?\d))"
)
# 유선 · 인터넷 전화(02 · 031~064 · 070). 하이픈 · 괄호 형식만 받는다 — 붙여 쓰거나 점 · 공백으로
# 나눈 숫자는 문서 · 공고 · 시간 표기와 구별되지 않는다(6차 검토: `02.1530.1600`, `02 2019 2020`).
# ★ 5차 검토 전에는 휴대폰만 봐서 `02-1234-5678` 이 그대로 나갔다.
_AREA = r"(?:2|[3-6][1-5]|70)"
_LANDLINE = re.compile(
    rf"(?<![\d.-])(?:\(0{_AREA}\)[ ]?|(?:\+82-?{_AREA}|0{_AREA})-)\d{{3,4}}-\d{{4}}(?![\d-]|호)"
)
# 앞에 오면 전화가 아니다 — 문서 · 공고 · 접수 번호, 그리고 계좌(계좌 규칙이 받는다).
_LANDLINE_ANTI = ("문서", "공고", "접수", "주문", "송장", "사번", "일련", "계좌", "입금", "은행")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_MAC = re.compile(r"(?<![0-9A-F])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![0-9A-F])", re.IGNORECASE)


def detect_common_pii(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _EMAIL.finditer(text):
        findings.append(Finding("L-CM-03", match.span(), 0.8, "L0", "email_format"))
    for match in _PHONE.finditer(text):
        findings.append(Finding("L-CM-02", match.span(), 0.8, "L0", "phone_format"))
    for match in _LANDLINE.finditer(text):
        before = text[max(0, match.start() - 12) : match.start()]
        if before.endswith("제") or any(word in before for word in _LANDLINE_ANTI):
            continue
        findings.append(Finding("L-CM-02", match.span(), 0.8, "L0", "landline_format"))
    for match in _CARD.finditer(text):
        if luhn_valid(match.group(0)):
            findings.append(Finding("L-CM-01", match.span(), 1.0, "L0", "luhn"))
    for match in _IPV4.finditer(text):
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        findings.append(Finding("L-CM-04", match.span(), 0.8, "L0", "ipv4_format"))
    for match in _MAC.finditer(text):
        findings.append(Finding("L-CM-04", match.span(), 0.8, "L0", "mac_format"))
    return findings
