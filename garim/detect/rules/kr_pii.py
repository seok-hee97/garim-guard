"""High-precision Korean identifier recognizers."""

from __future__ import annotations

import re

from garim.detect.rules.checksum import (
    business_checksum_valid,
    corporation_checksum_valid,
    rrn_checksum_valid,
    rrn_date_sex_valid,
)
from garim.models import Finding

_RRN = re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")
_PASSPORT = re.compile(r"(?<![A-Z0-9])[MSRO]\d{8}(?![A-Z0-9])", re.IGNORECASE)
_DRIVER = re.compile(r"(?<!\d)(?:1[1-9]|2[0-8])[- ]\d{2}[- ]\d{6}[- ]\d{2}(?!\d)")
_BUSINESS = re.compile(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{5}(?!\d)")
_CORPORATION = re.compile(r"(?<!\d)\d{6}[- ]?\d{7}(?!\d)")
_NHI = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)\d{2,6}(?:[- ]\d{2,6}){1,4}(?!\d)")

_RRN_ANCHORS = ("주민등록번호", "주민 번호", "주민번호", "내국인등록번호", "등록번호")
_FRN_ANCHORS = ("외국인등록번호", "외국인 등록번호", "외국인번호", "등록번호")
_ANTI_ANCHORS = ("주문", "송장", "운송장", "사번", "사원번호", "시리얼", "일련번호")


def _near_anchor(text: str, span: tuple[int, int], anchors: tuple[str, ...]) -> bool:
    start, end = span
    window = text[max(0, start - 20) : min(len(text), end + 5)]
    anchored = any(anchor in window for anchor in anchors)
    excluded = any(word in window for word in _ANTI_ANCHORS)
    return anchored and not excluded


def _finding(code: str, match: re.Match[str], confidence: float, rule: str) -> Finding:
    return Finding(code, match.span(), confidence, "L0", rule)


def detect_kr_pii(text: str, *, rrn_anchor: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for match in _RRN.finditer(text):
        value = match.group(0)
        if not rrn_date_sex_valid(value):
            continue
        gender = re.sub(r"\D", "", value)[6]
        if gender in "1234":
            if rrn_checksum_valid(value):
                findings.append(_finding("L-KR-01", match, 1.0, "rrn_checksum"))
            elif rrn_anchor and _near_anchor(text, match.span(), _RRN_ANCHORS):
                findings.append(_finding("L-KR-01", match, 0.6, "rrn_anchor"))
        elif rrn_anchor and _near_anchor(text, match.span(), _FRN_ANCHORS):
            # The statutory FRN layout has no check digit. Never apply the RRN checksum here.
            findings.append(_finding("L-KR-02", match, 0.6, "frn_anchor_no_checksum"))

    for match in _BUSINESS.finditer(text):
        if business_checksum_valid(match.group(0)):
            findings.append(_finding("L-KR-05", match, 1.0, "business_checksum"))
    for match in _CORPORATION.finditer(text):
        if corporation_checksum_valid(match.group(0)):
            findings.append(_finding("L-KR-06", match, 1.0, "corporation_checksum"))
    for match in _PASSPORT.finditer(text):
        findings.append(_finding("L-KR-03", match, 0.8, "passport_format"))
    for match in _DRIVER.finditer(text):
        findings.append(_finding("L-KR-04", match, 0.8, "driver_license_format"))
    for match in _NHI.finditer(text):
        if _near_anchor(text, match.span(), ("건강보험", "보험증", "건보")):
            findings.append(_finding("L-KR-07", match, 0.8, "nhi_anchor"))
    for match in _ACCOUNT.finditer(text):
        number = re.sub(r"\D", "", match.group(0))
        if 10 <= len(number) <= 16 and _near_anchor(text, match.span(), ("계좌", "입금", "은행")):
            findings.append(_finding("L-KR-08", match, 0.8, "account_anchor"))
    return findings
