"""Common PII recognizers with deterministic validation."""

from __future__ import annotations

import ipaddress
import re

from garim.detect.rules.checksum import luhn_valid
from garim.models import Finding

# ★ 끝 경계 — 초판 `(?![A-Z0-9_.-])` 는 문장 끝 마침표까지 거부해서 `kim@example.com.` 이
#   통째로 탐지되지 않았다(4차 검토). `.` · `_` 는 뒤에 영숫자가 이어질 때만(`x.y_z` 식별자) 막는다.
# ★ 앞 경계에 `%` 가 없어서 `%` 가 이어진 곳(URL 인코딩)의 모든 위치가 시작점이 됐다 — 입력
#   길이의 제곱. `%EB%8B%A4` 20KB 에 3초, `%` 4만 자에 29초(전수 검토 2026-10-28).
_EMAIL = re.compile(
    r"(?<![A-Z0-9_.%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24}(?![A-Z0-9-])(?![._][A-Z0-9])",
    re.IGNORECASE,
)
# 점 구분(`010.1234.5678`)은 뒤에 `.숫자` 가 이어지면(버전 문자열) 받지 않는다. 그 검사는
# 점 구분에만 건다 — ★ 6차 검토: 모든 구분자에 걸었더니 `010-1234-5678.3시 이후` 의 번호가
# 통째로 나갔다.
_PHONE = re.compile(
    r"(?<!\d)(?:\+82[- .]?1[016789]|01[016789])"
    r"(?:[- ]?\d{3,4}[- ]?\d{4}(?!\d)|\.\d{3,4}\.\d{4}(?!\.?\d))"
    # 괄호 · 국가번호 변이(`(010) 1234-5678`, `82-10-…`, `+82 (0)10-…`) — 전수 검토 2026-10-28.
    r"|\(01[016789]\) ?\d{3,4}-\d{4}(?!\d)"
    r"|(?<![\d+])82-1[016789]-\d{3,4}-\d{4}(?!\d)"
    r"|\+82 ?\(0\) ?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)"
)
# 유선 · 인터넷 전화(02 · 031~064 · 070). 하이픈 · 괄호 형식만 받는다 — 붙여 쓰거나 점 · 공백으로
# 나눈 숫자는 문서 · 공고 · 시간 표기와 구별되지 않는다(6차 검토: `02.1530.1600`, `02 2019 2020`).
# ★ 5차 검토 전에는 휴대폰만 봐서 `02-1234-5678` 이 그대로 나갔다.
_AREA = r"(?:2|[3-6][1-5]|70)"
_LANDLINE = re.compile(
    # 명함식 `02)1234-5678` 도 받는다(전수 검토).
    # 여는 괄호 없는 꼴은 붙여 쓰고 줄 머리가 아닐 때만 — `02) 2024-1015 회의` 는 번호 목록이다
    # (재검토). 명함 표기는 대개 앞에 `Tel.` · `전화` 가 온다.
    rf"(?<![\d.-])(?:\(0{_AREA}\)[ ]?|(?<!^)(?<!\n)0{_AREA}\)|(?:\+82-?{_AREA}|0{_AREA})-)"
    rf"\d{{3,4}}-\d{{4}}(?![\d-]|호)"
)
# 앞에 오면 전화가 아니다 — 문서 · 공고 · 접수 번호, 그리고 계좌(계좌 규칙이 받는다).
_LANDLINE_ANTI = ("문서", "공고", "접수", "주문", "송장", "사번", "일련", "계좌", "입금", "은행")
# 카드 — 같은 구분자로 나눈 4-4-4-4(+1~3) · 4-6-5 · 4-6-4, 또는 붙여 쓴 13~19자리.
# ★ 처음엔 아무 구분자 · 아무 묶음이나 받아서(전수 검토 2026-10-28)
#   - 이웃한 전화번호 둘(`010-4828-5819 010-9188-1075`)을 카드 하나로 삼켰고,
#   - 뒤에 붙은 유효기간 · CVC(`… 1111 12/28`)까지 한 덩어리로 보고 Luhn 이 실패해 카드를 놓쳤고,
#   - 에포크 밀리초 · 스노플레이크 ID · 바코드의 10%를 카드로 가렸다.
#   카드 번호는 0 으로 시작하지 않는다. 붙여 쓴 숫자는 발급사 대역(IIN)이 맞거나 앵커가
#   있어야 한다.
#   (재검토: 같은 구분자를 요구하면 `4111 0000-0000 0008` 을, 하이픈 이웃을 막으면
#   `카드번호-4111…` · `4111…-1228` 을 놓쳤다 — 0 시작 금지만으로 이웃 전화번호는 막힌다.)
_CARD_GROUPED = re.compile(
    r"(?<!\d)(?<!\d-)[1-9]\d{3}[ -]"
    r"(?:\d{4}[ -]\d{4}[ -]\d{4}(?P<tail>[ -]\d{1,3})?|\d{6}[ -]\d{4,5})(?!\d)"
)
_CARD_BARE = re.compile(r"(?<!\d)(?<!\d-)[1-9]\d{12,18}(?!\d)")
_CARD_ANCHOR = re.compile(r"(?:카드|신용|체크|결제|[Cc]ard|CARD)[^\n]{0,12}$")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# 버전 · 조항 번호(`버전 1.0.0.0`, `제3.1.2.1항`)와 루프백 · 미지정 주소는 가릴 주소가 아니다.
# 낱말 경계를 둔다 — `srv 10.0.0.5` · `dev 10.1.2.3` 의 끝 `v` 는 버전 표기가 아니다(재검토).
_VERSION_BEFORE = re.compile(r"(?:(?<![A-Za-z])(?:[Vv]er(?:sion)?|[Vv])|버전|제)\s?$")
_MAC = re.compile(r"(?<![0-9A-F])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![0-9A-F])", re.IGNORECASE)


def _iin_plausible(number: str) -> bool:
    """발급사 대역과 길이가 맞는가 — 붙여 쓴 숫자에만 쓴다."""
    length, head2, head4 = len(number), number[:2], int(number[:4])
    if number[0] == "4":
        return length in (13, 16, 19)
    if "51" <= head2 <= "55" or 2221 <= head4 <= 2720:
        return length == 16
    if head2 in ("34", "37"):
        return length == 15
    if head2 in ("30", "36", "38"):
        return length in (14, 16)
    if head2 == "35":
        return length == 16
    if head4 == 6011 or head2 in ("62", "64", "65"):
        return 16 <= length <= 19
    return False


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
    for match in _CARD_GROUPED.finditer(text):
        start, end = match.span()
        if not luhn_valid(match.group(0)) and match.group("tail"):
            end -= len(match.group("tail"))  # 뒤에 붙은 숫자를 떼고 16자리로 다시 본다
        if luhn_valid(text[start:end]):
            findings.append(Finding("L-CM-01", (start, end), 1.0, "L0", "luhn"))
    for match in _CARD_BARE.finditer(text):
        number = match.group(0)
        anchored = _CARD_ANCHOR.search(text[max(0, match.start() - 20) : match.start()])
        if luhn_valid(number) and (_iin_plausible(number) or anchored):
            findings.append(Finding("L-CM-01", match.span(), 1.0, "L0", "luhn"))
    for match in _IPV4.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        before = text[max(0, match.start() - 8) : match.start()]
        after = text[match.end() : match.end() + 1]
        version = _VERSION_BEFORE.search(before) or after in ("항", "조", "절")
        if address.is_loopback or address.is_unspecified or version:
            continue
        findings.append(Finding("L-CM-04", match.span(), 0.8, "L0", "ipv4_format"))
    for match in _MAC.finditer(text):
        findings.append(Finding("L-CM-04", match.span(), 0.8, "L0", "mac_format"))
    return findings
