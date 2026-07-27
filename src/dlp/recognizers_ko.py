"""한국어 PII 인식기 (정규식 기반). RegexVault 가 사용.

⚠️ 주민등록번호는 체크섬 하드리젝트 금지(2020-10 이후 발급분 검증 불가) — 형식만 잡는다.
   `\b` 앵커는 한국어에서 부정확할 수 있어 숫자/하이픈 경계로 잡는다.

엔티티: KR_RRN(주민), KR_PHONE(전화), KR_CARD(카드), KR_ACCOUNT(계좌), EMAIL.
python-stdnum 체크섬은 신뢰도 가점용이며 여기서는 형식 매칭만 한다(fail-closed 원칙:
형식이 맞으면 비식별한다 — 놓치는 것보다 과비식별이 안전).
"""

from __future__ import annotations

import re

# 주민등록번호: 6자리-7자리. 앞자리 생년월일, 뒷자리 첫숫자 1-4(내국인)·5-8(외국인).
KR_RRN = re.compile(r"\d{6}-[1-8]\d{6}")
# 휴대폰/일반전화: 010-XXXX-XXXX, 02-XXX(X)-XXXX 등
KR_PHONE = re.compile(r"01[016789]-\d{3,4}-\d{4}|0\d{1,2}-\d{3,4}-\d{4}")
# 신용카드: 4-4-4-4 (하이픈/공백)
KR_CARD = re.compile(r"\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}")
# 계좌번호: 은행별 상이 → 3-6자리 그룹 2~3개 하이픈
KR_ACCOUNT = re.compile(r"\d{2,3}-\d{2,6}-\d{4,6}")
EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# 순서 = 우선순위. 더 구체적인(긴) 패턴을 먼저 둬 겹침에서 이긴다.
# RRN 을 CARD/ACCOUNT 보다 먼저(주민번호가 계좌 패턴에 부분매칭될 수 있음).
KO_RECOGNIZERS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", EMAIL),
    ("KR_RRN", KR_RRN),
    ("KR_CARD", KR_CARD),
    ("KR_PHONE", KR_PHONE),
    ("KR_ACCOUNT", KR_ACCOUNT),
]
