"""R2 합성 PII 음성 범주 — PREREG_L1_R2 C4.

R1 의 제품 경로 PII 오탐은 둘이었다(RESULTS_L1 §4 · §5-①).

- **12자리 운송장번호를 계좌번호로 가렸다.** 계좌도 10~14자리이고 공통 체크섬이 없어 문맥 단어가
  유일한 단서다. R1 합성에는 계좌 양성(표기 변형 포함)만 있고 **같은 자릿수의 숫자가 다른 번호
  문맥에 나오는 음성**이 없었다 — R1 의 짝 설계(§5.3.2)는 주민번호/주문번호뿐이었다. 그래서 같은
  숫자를 계좌 문맥(양성)과 다른 번호 문맥(음성)에 넣은 짝을 만든다.
- **낯선 문자 구성(한자 · 동형 문자 · 어절 쪼개기)에 낮은 확신도의 PII 를 냈다.** 학습에 그런 글이
  PII 없이 나온 적이 없었다. PII 없는 정상문에 은닉을 걸고, 해시 · hex · base64 문자열을 문맥과
  함께 O 로 넣는다.

기존 50,000행(`generate_pii_corpus`)은 건드리지 않는다 — RNG 를 따로 써서 R1 합성 PII 와 그
test 가 바이트 그대로다. 분할은 family 해시 70/10/20 이다(기존 90/5/5 로는 test family 가
한두 개다).
문맥 문장은 bc-pii-ko 보강 음성(R1 에서 오탐 내역을 본 셋)의 문형을 피해 새로 썼다.
"""

from __future__ import annotations

import base64
import hashlib
import random
import string
from collections import defaultdict

from garim.detect.rules import detect_common_pii, detect_kr_pii
from garim.training.data import CorpusRecord, CorpusSpan, Split, validate_corpus
from garim.training.hiding import (
    HANJA,
    HOMOGLYPH,
    JAMO,
    SPACE_BREAK,
    TENSIFY,
    ZWSP,
    apply_hiding,
)
from garim.training.synthetic import _rrn_check_digit

SOURCE = "garim-pii-negative-r2"
NUMERIC_NEGATIVES = 4_000  # 그중 ACCOUNT_PAIRS 행이 짝 음성
ACCOUNT_PAIRS = 1_000
OBFUSCATED_NEGATIVES = 3_000

_NUMERIC_CONTEXTS = (
    "택배 송장 {num} 기준으로 도착 예정일을 확인해 주세요.",
    "주문하신 상품의 주문번호는 {num}이며 내일 출고됩니다.",
    "카드 결제 승인 {num} 건에 대해 영수증 재발행을 요청합니다.",
    "민원 처리 현황은 접수 {num} 으로 조회하실 수 있습니다.",
    "호텔 예약 확인 번호 {num}로 체크인 시간을 변경하고 싶습니다.",
    "멤버십 회원 번호 {num}의 적립 포인트를 확인해 주세요.",
    "할인 쿠폰 코드 {num}가 적용되지 않는다는 문의가 들어왔습니다.",
    "법원 사건번호 {num} 관련 서류 목록을 정리해 주세요.",
    "문서 관리 번호 {num}번 공문의 회신 기한을 알려 주세요.",
    "은행 창구 대기 번호표 {num}를 받았는데 순서가 언제쯤인가요?",
    "공연 좌석 번호 {num}의 시야 정보를 알려 주세요.",
    "제품 시리얼 {num} 장비의 보증 기간을 확인해 주세요.",
    "영수증 번호 {num}로 교환 접수를 진행해 주세요.",
    "온라인 거래 번호 {num} 건의 환불 진행 상황을 알려 주세요.",
    "배차 번호 {num} 차량의 출발 시간을 확인해 주세요.",
    "등기우편 {num}이 반송되었다는 안내를 받았습니다.",
    "자산관리 태그 {num}이 붙은 모니터를 회수해 주세요.",
    "행사 입장 티켓 {num}을 모바일로 다시 보내 주세요.",
    "상품권 교환 번호 {num}의 사용 기한을 알려 주세요.",
    "주차 정산 번호 {num}로 할인 등록을 부탁드립니다.",
    "설비 관리 번호 {num} 기계의 점검 이력을 정리해 주세요.",
    "모델 번호 {num} 제품의 사용 설명서를 찾아 주세요.",
    "제조 로트 번호 {num} 제품의 출하 일자를 확인해 주세요.",
    "견적 요청 번호 {num}에 대한 회신 초안을 써 주세요.",
    "구매 요청서 {num}번의 결재 상태를 알려 주세요.",
    "출장 신청 번호 {num}의 승인 여부를 확인해 주세요.",
    "사내 교육 과정 코드 {num}의 수강 인원을 알려 주세요.",
    "프로젝트 과제 번호 {num}의 중간 보고 일정을 정리해 주세요.",
    "A/S 접수 건 {num}의 수리 진행 상황이 궁금합니다.",
    "항공권 예약 번호 {num}의 좌석을 창가로 바꾸고 싶습니다.",
    "고지서 납부 번호 {num}의 납부 기한을 알려 주세요.",
    "보험 청구 접수 {num} 건의 필요 서류를 알려 주세요.",
    "택시 영수증 승인 {num}을 경비 처리에 첨부했습니다.",
    "정기 구독 번호 {num}의 배송 주기를 바꾸고 싶습니다.",
    "렌터카 계약 번호 {num}의 반납 장소를 변경해 주세요.",
    "도서관 대출 번호 {num} 책의 반납 기한을 연장해 주세요.",
    "시험 수험 번호 {num}의 고사장 위치를 알려 주세요.",
    "물류센터 입고 번호 {num} 화물의 검수 결과를 공유해 주세요.",
    "세금계산서 승인 번호 {num}의 발행 일자를 확인해 주세요.",
    "회의실 예약 번호 {num}의 시간을 한 시간 늦춰 주세요.",
    "고객 상담 이력 번호 {num}의 처리 결과를 요약해 주세요.",
    "공사 현장 작업 지시 번호 {num}의 완료 보고서를 써 주세요.",
)
# ★ GPU 전 독립 검토 — 운송장·송장 문맥은 `pii-num-00` 하나뿐이었고 그것이 해시로 test 에 떨어져
#   학습에는 송장 문맥이 0 이었다. R1 제품 경로 오탐의 대부분이 바로 그 문맥(12자리 운송장번호)이다.
#   새로 쓴 문맥을 train 에 고정한다(bc-pii-ko 의 "배송 조회가 가능합니다" 문형은 쓰지 않는다).
_SHIPPING_CONTEXTS = (
    "운송장 {num} 화물이 아직 집하되지 않았다고 합니다.",
    "반품 송장 {num}을 출력해서 상자 위에 붙여 주세요.",
    "편의점 택배 운송장 {num}의 접수 시각을 확인해 주세요.",
    "해외 배송 송장번호 {num} 건의 통관 진행 상황을 알려 주세요.",
)
_ACCOUNT_CONTEXTS = (
    "환불받으실 계좌번호 {num}으로 입금해 드리겠습니다.",
    "급여 이체 계좌를 {num}로 변경해 주세요.",
    "거래처 대금은 계좌 {num} 앞으로 송금 부탁드립니다.",
    "보증금 반환 계좌는 {num}입니다.",
    "자동이체 출금 계좌 {num}의 잔액 부족 안내를 받았습니다.",
    "경조사비는 계좌번호 {num}로 보내 주시면 됩니다.",
    "회비 입금 계좌 {num} 확인 부탁드립니다.",
    "정산금은 은행 계좌 {num}로 지급됩니다.",
    "법인 통장 계좌번호 {num}를 거래처 등록에 사용해 주세요.",
    "출장비 지급 계좌를 {num}로 등록했습니다.",
    "중고 거래 대금은 제 계좌 {num}로 보내 주세요.",
    "등록금 납부용 계좌 {num}를 안내드립니다.",
)
# PII 가 없는 정상문 — 은닉을 걸어 "낯선 문자 구성 = PII 아님" 을 가르친다. 공격 쪽 정상 bank
# (`benign_bank`)와 겹치지 않게 따로 썼다. 겹치면 `t4-r2-test` 의 문장이 PII 학습에서 보인다.
_PLAIN = (
    "다음 주 화요일 오전에 팀 전체 회의가 있습니다.",
    "새로 입고된 사무용품을 창고 선반에 정리했습니다.",
    "오늘 오후 세 시에 정전 점검이 예정되어 있습니다.",
    "이번 분기 목표는 고객 만족도를 높이는 것입니다.",
    "점심 메뉴로 비빔밥과 된장찌개 중에서 골라 주세요.",
    "보고서 초안을 금요일까지 공유해 주시기 바랍니다.",
    "엘리베이터 정기 점검으로 오전에는 계단을 이용해 주세요.",
    "신제품 출시 행사는 다음 달 초에 열릴 예정입니다.",
    "회의실 프로젝터가 고장 나서 수리를 요청했습니다.",
    "주말 동안 서버 이전 작업이 진행됩니다.",
    "올해 워크숍은 바닷가 근처 연수원에서 진행합니다.",
    "사무실 냉방 온도를 이십육 도로 맞춰 주세요.",
    "거래처 방문 일정이 하루 앞당겨졌습니다.",
    "새 직원 환영 모임을 저녁에 준비하고 있습니다.",
    "분리수거 요일이 수요일로 바뀌었습니다.",
    "홍보 영상 촬영은 실내 스튜디오에서 합니다.",
    "연말 정산 서류 제출 기간이 시작되었습니다.",
    "프린터 토너가 떨어져서 주문을 넣었습니다.",
    "고객 설문 응답률이 지난달보다 올랐습니다.",
    "휴게실 커피 머신 청소를 매주 월요일에 합니다.",
    "다음 교육은 온라인으로 진행할 예정입니다.",
    "출장 보고서는 사내 게시판에 올려 주세요.",
    "창고 재고 조사는 이번 주 목요일에 실시합니다.",
    "건물 출입구 공사로 후문을 이용해 주시기 바랍니다.",
    "회사 체육대회 종목 의견을 모으고 있습니다.",
    "새 카탈로그 디자인 시안이 두 가지 나왔습니다.",
    "봄맞이 대청소를 금요일 오후에 합니다.",
    "업무용 노트북 교체 신청을 받습니다.",
    "주간 회의 시간이 오전 열 시로 변경되었습니다.",
    "협력사 간담회 장소를 다시 정해야 합니다.",
    "사내 도서 대여 기간은 이 주입니다.",
    "비 소식이 있으니 우산을 챙겨 오세요.",
    "신규 프로젝트 착수 회의는 본관 삼층에서 열립니다.",
    "제품 포장 디자인을 친환경 소재로 바꿀 계획입니다.",
)
_HASH_CONTEXTS = (
    "배포 커밋 {hex40} 기준으로 변경 사항을 정리해 주세요.",
    "다운로드한 설치 파일의 SHA-256 값은 {hex64}입니다.",
    "빌드 산출물 체크섬 {hex64}이 배포 노트와 일치하는지 확인해 주세요.",
    "첨부 이미지는 base64로 인코딩된 {b64} 형식입니다.",
    "문서 버전 해시 {hex40}을 변경 이력에 남겨 주세요.",
    "캐시 키 {hex32}가 만료되었다는 경고가 떴습니다.",
    "테스트 픽스처 데이터는 {b64} 로 저장되어 있습니다.",
    "패키지 무결성 값 {hex64}을 확인했습니다.",
    "로고 파일 미리보기 데이터 {b64} 를 디자인팀에 전달해 주세요.",
)
# 정상문에 거는 은닉 6종 — T4 축 안 7종에서 b64 를 뺀 것(b64 는 해시 문맥이 맡는다).
_HIDINGS = (HANJA, HOMOGLYPH, SPACE_BREAK, JAMO, ZWSP, TENSIFY)
# 동형 문자는 바꿀 라틴 글자가 있어야 한다 — T4 정상 셀과 같은 중립 약어를 심는다.
_LATIN_MARKERS = ("CSV", "PDF", "API", "JSON")


def _family_split(family: str) -> Split:
    if family.startswith("pii-num-ship-"):
        return "train"
    bucket = int(hashlib.sha256(f"pii-neg-r2:{family}".encode()).hexdigest()[:8], 16) % 10
    if bucket < 2:
        return "test"
    return "validation" if bucket == 2 else "train"


def _l0_silent(text: str) -> bool:
    """L0 규칙이 PII 를 하나도 안 잡는가.

    ★ 무작위 숫자는 10개 중 하나꼴로 사업자·법인번호 체크섬이나 Luhn 을 통과한다. L0 는 체크섬이
      맞으면 문맥과 무관하게 잡도록 설계됐으므로(§3.2.3), 그런 음성은 L1 이 무엇을 배우든 제품
      경로 오탐으로 남는다. R1 기준선에서 숫자 문맥 음성 test 의 4.6% 가 그랬고, 그러면 O3(≤ 2%)
      는 통과할 수 없다. 이 범주가 재는 것은 L1 의 문맥 판단이므로 L0 가 잡는 모양은 뺀다.
    """
    return not (detect_kr_pii(text) + detect_common_pii(text))


def _number(
    rng: random.Random, length: int, *, hyphen: bool, leading_zero: bool = False
) -> str:
    """음성 번호. 다른 PII 형식으로 읽힐 모양은 피한다 — 이 범주가 가르치려는 것은 문맥이다.

    `leading_zero` 면 앞자리를 0 으로 둔다. R1 의 나머지 bc 오탐(`04760327` 의 `04` 를 전화로)이
    그 모양이었다. 휴대전화 접두 `010` 은 언제나 뺀다.
    """
    while True:
        digits = "".join(rng.choice(string.digits) for _ in range(length))
        if leading_zero:
            digits = "0" + digits[1:]
        if digits.startswith("010") or (digits.startswith("0") and not leading_zero):
            continue
        if length == 13 and _rrn_check_digit(digits[:12]) == digits[12]:
            continue  # 체크섬을 통과하는 주민번호 모양
        if not hyphen:
            return digits
        # 세 덩어리, 가운데 3~4자리 — 사업자등록번호(3-2-5) · 주민번호(6-7) 모양이 나올 수 없다.
        cut = rng.choice([cut for cut in (3, 4, 5) if cut < length - 3])
        second = rng.choice([size for size in (3, 4) if cut + size < length])
        return f"{digits[:cut]}-{digits[cut:cut + second]}-{digits[cut + second:]}"


def _hidden(text: str, position: int) -> tuple[str, str]:
    """돌아가며 은닉을 건다. 걸리지 않는 기법은 다음 것으로 — 평문을 은닉 행이라 부르지 않는다."""
    for step in range(len(_HIDINGS)):
        technique = _HIDINGS[(position + step) % len(_HIDINGS)]
        carrier = text
        if technique == HOMOGLYPH:
            carrier = f"{_LATIN_MARKERS[position % len(_LATIN_MARKERS)]} 첨부: {text}"
        hidden, applied = apply_hiding(carrier, technique)
        if applied == technique:
            return hidden, applied
    return text, "none"


def _record(
    index: int,
    text: str,
    family: str,
    category: str,
    *,
    spans: list[CorpusSpan] | None = None,
    pair_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> CorpusRecord:
    return CorpusRecord(
        id=f"pii2-neg-{index:05d}",
        task="pii",
        text=text,
        spans=spans or [],
        source=SOURCE,
        split=_family_split(family),
        group_id=pair_id or f"pii2-row-{index:05d}",
        template_id=family,
        pair_id=pair_id,
        meta={
            "synthetic": True,
            "generator": "r2",
            "type": category,
            "family": family,
            "doc_shape": "prose",
            "segment_types": ["prose"],
            "may_match_a_real_identifier": bool(spans),
            **(extra or {}),
        },
    )


def generate_pii_negatives_r2(*, seed: int = 20261027) -> list[CorpusRecord]:
    rng = random.Random(seed)
    records: list[CorpusRecord] = []
    numeric = [
        *((f"pii-num-{i:02d}", text) for i, text in enumerate(_NUMERIC_CONTEXTS)),
        *((f"pii-num-ship-{i}", text) for i, text in enumerate(_SHIPPING_CONTEXTS)),
    ]
    accounts = [(f"pii-acct-{i:02d}", text) for i, text in enumerate(_ACCOUNT_CONTEXTS)]
    numeric_by_split: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for family, text in numeric:
        numeric_by_split[_family_split(family)].append((family, text))

    # 계좌 짝 — 짝은 한 분할 안에 있어야 하므로(`validate_corpus`) 음성 family 를 **같은 분할**
    # 에서 고른다. family 를 행마다 다른 ID 로 쪼개면 R1 의 §5-③ 결함이 되풀이된다.
    for pair_index in range(ACCOUNT_PAIRS):
        family, template = accounts[pair_index % len(accounts)]
        split = _family_split(family)
        partners = numeric_by_split[split]
        if not partners:
            raise AssertionError(f"no numeric context family in split {split!r} to pair with")
        partner_family, partner_template = partners[pair_index % len(partners)]
        digits = _number(rng, rng.randint(10, 14), hyphen=False)
        while not _l0_silent(partner_template.format(num=digits)):
            digits = _number(rng, rng.randint(10, 14), hyphen=False)
        pair_id = f"pii2-acct-pair-{pair_index:04d}"
        start = template.index("{num}")
        positive = template.format(num=digits)
        records.append(
            _record(
                len(records), positive, family, "account_pair_positive",
                spans=[CorpusSpan(start=start, end=start + len(digits), label="KR_ACCOUNT")],
                pair_id=pair_id,
            )
        )
        records.append(
            _record(len(records), partner_template.format(num=digits), partner_family,
                    "numeric_context_negative", pair_id=pair_id, extra={"paired": True})
        )
    for local_index in range(NUMERIC_NEGATIVES - ACCOUNT_PAIRS):
        family, template = numeric[local_index % len(numeric)]
        shape = {"hyphen": local_index % 2 == 1, "leading_zero": local_index % 8 == 0}
        number = _number(rng, rng.randint(8, 14), **shape)
        while not _l0_silent(template.format(num=number)):
            number = _number(rng, rng.randint(8, 14), **shape)
        records.append(
            _record(len(records), template.format(num=number), family,
                    "numeric_context_negative", extra={"paired": False})
        )
    obfuscated = [
        *((f"pii-plain-{i:02d}", text) for i, text in enumerate(_PLAIN)),
        *((f"pii-hash-{i:02d}", text) for i, text in enumerate(_HASH_CONTEXTS)),
    ]
    for local_index in range(OBFUSCATED_NEGATIVES):
        family, template = obfuscated[local_index % len(obfuscated)]
        if family.startswith("pii-hash"):
            text = ""
            while not text or not _l0_silent(text):
                text = template.format(
                    hex32=f"{rng.getrandbits(128):032x}",
                    hex40=f"{rng.getrandbits(160):040x}",
                    hex64=f"{rng.getrandbits(256):064x}",
                    b64=base64.b64encode(rng.randbytes(rng.randint(24, 48))).decode(),
                )
            hiding = "none"
        else:
            text, hiding = _hidden(template, local_index // len(obfuscated))
        records.append(
            _record(len(records), text, family, "obfuscated_negative", extra={"hiding": hiding})
        )
    noisy = [r.id for r in records if not r.spans and not _l0_silent(r.text)]
    if noisy:
        raise AssertionError(f"L0 fires on {len(noisy)} negative rows, e.g. {noisy[:3]}")
    validate_corpus(records)
    return records
