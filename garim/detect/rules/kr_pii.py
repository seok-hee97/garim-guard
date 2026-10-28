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
# 여권 — 구형 `M12345678` · 차세대 `M123A4567`. 둘 다 회원번호 · 모델명 · 해시와 모양이 겹치므로
# (6차 · 전수 검토: `d123e4567`, `S100E2000`, `회원번호 M00012345`) 대문자만,
# `여권` 앵커가 있을 때만 받는다.
_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[MSRO]\d{8}(?![A-Za-z0-9])")
_PASSPORT_NEW = re.compile(r"(?<![A-Za-z0-9])[MSRODG]\d{3}[A-Z]\d{4}(?![A-Za-z0-9])")
_PASSPORT_ANCHORS = ("여권", "passport", "Passport", "PASSPORT")
_DRIVER = re.compile(r"(?<!\d)(?:1[1-9]|2[0-8])[- ]\d{2}[- ]\d{6}[- ]\d{2}(?!\d)")
_BUSINESS = re.compile(r"(?<!\d)\d{3}([- ]?)\d{2}\1\d{5}(?!\d)")
_CORPORATION = re.compile(r"(?<!\d)\d{6}[- ]?\d{7}(?!\d)")
_NHI = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")
# 계좌 — 하이픈으로 나눈 묶음(카카오뱅크 4-2-7 까지) 또는 앵커 옆의 붙여 쓴 10~14자리.
# ★ 공백도 구분자로 받아서 뒤의 금액 · 날짜까지 먹었다(`계좌 110-123-456789 10만원` →
#   `<KR_ACCOUNT_1>만원`). 묶음 상한 6 은 카카오뱅크 7자리 묶음을 놓쳤다(전수 검토 2026-10-28).
_ACCOUNT = re.compile(r"(?<![\d-])\d{2,7}(?:-\d{2,7}){1,4}(?![\d-])")
_ACCOUNT_BARE = re.compile(r"(?<!\d)\d{10,14}(?!\d)")
_ACCOUNT_ANCHORS = (
    "계좌", "입금", "은행", "이체", "송금", "예금주", "뱅크", "농협", "신한", "수협", "신협",
    "새마을", "우체국", "씨티", "SC제일",
)
# 흔한 낱말과 같은 은행 이름(`국민 여러분`, `우리 회사`)은 번호 바로 앞에 있을 때만 앵커다.
_BANK_RIGHT_BEFORE = re.compile(r"(?:국민|우리|하나|기업|산업|부산|대구|경남|광주|전북|제주)[ :]?$")
_PHONE_SHAPED = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")

_RRN_ANCHORS = ("주민등록번호", "주민 번호", "주민번호", "내국인등록번호", "등록번호")
_FRN_ANCHORS = ("외국인등록번호", "외국인 등록번호", "외국인번호", "등록번호")
_CORPORATION_ANCHORS = ("법인등록번호", "법인 등록번호", "법인번호", "등기번호")
_BUSINESS_ANCHORS = ("사업자", "등록번호", "business", "Business", "BRN")
_ANTI_ANCHORS = ("주문", "송장", "운송장", "사번", "사원번호", "시리얼", "일련번호")
# `등록번호` 가 더 긴 다른 번호 이름의 꼬리일 때는 그 번호의 앵커가 아니다 — 법인 · 사업자
# 등록번호가 주민 · 외국인등록번호로 block 됐다(전수 검토: 합성 법인번호 300건 중 190건).
_ANCHOR_TAILS = {"등록번호": ("법인", "사업자", "법인 ", "사업자 ")}
_ANCHOR_BEFORE = 25
_ANCHOR_AFTER = 5


def _nearest(text: str, span: tuple[int, int], words: tuple[str, ...]) -> int | None:
    """span 앞 25자 · 뒤 5자 안에서 가장 가까운 낱말까지의 거리."""
    start, end = span
    best: int | None = None
    for word in words:
        blocked = _ANCHOR_TAILS.get(word, ())
        before = text[max(0, start - _ANCHOR_BEFORE) : start]
        offset = max(0, start - _ANCHOR_BEFORE)
        position = before.rfind(word)
        while position != -1:
            absolute = offset + position
            if not any(text[:absolute].endswith(tail) for tail in blocked):
                distance = start - (absolute + len(word))
                best = distance if best is None else min(best, distance)
                break
            position = before.rfind(word, 0, position)
        after = text[end : end + _ANCHOR_AFTER]
        if (position := after.find(word)) != -1:
            best = position if best is None else min(best, position)
    return best


def _near_anchor(text: str, span: tuple[int, int], anchors: tuple[str, ...]) -> bool:
    """앵커가 있고, 제외 낱말이 있더라도 앵커가 더 가까울 때.

    ★ 처음엔 창 안에 제외 낱말이 하나라도 있으면 앵커를 무시했다 — `사번 A1, 외국인등록번호
      900101-5…` 가 allow 였다(전수 검토). 외국인등록번호는 체크섬이 없어 앵커가 유일한 길이다.
    """
    anchor = _nearest(text, span, anchors)
    if anchor is None:
        return False
    excluded = _nearest(text, span, _ANTI_ANCHORS)
    return excluded is None or anchor < excluded


def _finding(code: str, match: re.Match[str], confidence: float, rule: str) -> Finding:
    return Finding(code, match.span(), confidence, "L0", rule)


def detect_kr_pii(text: str, *, rrn_anchor: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for match in _RRN.finditer(text):
        value = match.group(0)
        if not rrn_date_sex_valid(value):
            continue
        corporation = _nearest(text, match.span(), _CORPORATION_ANCHORS)
        personal = _nearest(text, match.span(), _RRN_ANCHORS + _FRN_ANCHORS)
        if corporation is not None and (personal is None or corporation <= personal):
            continue  # `법인등록번호: 110111-…` — 같은 모양의 법인번호다(아래 법인 규칙이 받는다)
        gender = re.sub(r"\D", "", value)[6]
        if gender in "1234":
            if rrn_checksum_valid(value):
                findings.append(_finding("L-KR-01", match, 1.0, "rrn_checksum"))
            elif rrn_anchor and _near_anchor(text, match.span(), _RRN_ANCHORS):
                findings.append(_finding("L-KR-01", match, 0.6, "rrn_anchor"))
        elif rrn_anchor and _near_anchor(text, match.span(), _FRN_ANCHORS):
            # The statutory FRN layout has no check digit. Never apply the RRN checksum here.
            findings.append(_finding("L-KR-02", match, 0.6, "frn_anchor_no_checksum"))

    # ★ 붙여 쓴 숫자는 체크섬(1/10)만으로 받지 않는다 — 에포크 초 · 송장 · 바코드의 10~20%가
    #   사업자 · 법인번호로 가려졌다(전수 검토). 하이픈 형식이거나 앵커가 있어야 한다.
    for match in _BUSINESS.finditer(text):
        hyphenated = match.group(1) == "-"
        if business_checksum_valid(match.group(0)) and (
            hyphenated or _near_anchor(text, match.span(), _BUSINESS_ANCHORS)
        ):
            findings.append(_finding("L-KR-05", match, 1.0, "business_checksum"))
    for match in _CORPORATION.finditer(text):
        # 법인번호는 주민번호와 모양(6-7)이 같다 — 앵커 없이는 둘을 가르지 못한다.
        if corporation_checksum_valid(match.group(0)) and _near_anchor(
            text, match.span(), _CORPORATION_ANCHORS
        ):
            findings.append(_finding("L-KR-06", match, 1.0, "corporation_checksum"))
    for match in _PASSPORT.finditer(text):
        if _near_anchor(text, match.span(), _PASSPORT_ANCHORS):
            findings.append(_finding("L-KR-03", match, 0.8, "passport_format"))
    for match in _PASSPORT_NEW.finditer(text):
        if _near_anchor(text, match.span(), _PASSPORT_ANCHORS):
            findings.append(_finding("L-KR-03", match, 0.8, "passport_new_format"))
    for match in _DRIVER.finditer(text):
        findings.append(_finding("L-KR-04", match, 0.8, "driver_license_format"))
    for match in _NHI.finditer(text):
        if _near_anchor(text, match.span(), ("건강보험", "보험증", "건보")):
            findings.append(_finding("L-KR-07", match, 0.8, "nhi_anchor"))
    for pattern in (_ACCOUNT, _ACCOUNT_BARE):
        for match in pattern.finditer(text):
            number = re.sub(r"\D", "", match.group(0))
            if not 10 <= len(number) <= 16 or _PHONE_SHAPED.fullmatch(match.group(0)):
                continue
            bank_right_before = pattern is _ACCOUNT and bool(
                _BANK_RIGHT_BEFORE.search(text[max(0, match.start() - 6) : match.start()])
            )
            if bank_right_before or _near_anchor(text, match.span(), _ACCOUNT_ANCHORS):
                findings.append(_finding("L-KR-08", match, 0.8, "account_anchor"))
    return findings
