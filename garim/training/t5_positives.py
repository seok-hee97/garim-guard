"""§5.4 T5 — **생성기를 한 번도 안 거친 양성.** §7.1.0 이 H1 재현율의 유일한 근거라 적었다.

합성 생성기로 만든 양성으로 재현율을 재면 항등식이 된다 — 같은 규칙이 만들고 같은
규칙이 잡는다. T5 는 그 고리를 끊기 위해 **생성기 밖에서** 온 값만 담는다.

## 값의 출처를 셋으로 나눠 적는다

| 출처 | 예 | 왜 안전한가 |
|---|---|---|
| **공개 문서 테스트 벡터** | 카드사 테스트 BIN, 공개 사업자번호 | 발급 기관이 공개한 값이다 |
| **형식 규칙에서 손으로 유도** | 무체크섬 4종(여권·면허·건보·계좌) | 형식 규칙에서 직접 만들었다 |
| **자연어 표기 변형** | "구공영일영일 다시 일이삼사" | 사람이 실제로 쓰는 표기 |

⚠️ **여기 어떤 값도 실재 개인의 것이 아니다.** 체크섬을 통과하는 난수는 우연히 실재
식별자와 일치할 수 있으므로(§5.3 의 같은 경고) 이 모듈은 `data/` 밖으로 나가는
경로에 쓰지 않는다.

⚠️ §5.4 가 정한 총 1,150 중 여기서 만드는 것은 **손으로 열거 가능한 슬라이스**다.
`3세대 125`·`문맥 의존 100` 처럼 사람이 문장을 써야 하는 칸은 규모만 잡아 두고
비워 둔다 — 채운 척하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator

from garim.training.data import CorpusRecord, CorpusSpan

# ── 공개 문서의 테스트 벡터 ────────────────────────────────────────────
# 카드: 각 카드사가 문서에 싣는 테스트 BIN. Luhn 을 통과하지만 발급되지 않는다.
_PUBLIC_CARDS = (
    "4242 4242 4242 4242",  # Stripe 문서
    "4111 1111 1111 1111",  # Visa 테스트
    "5555 5555 5555 4444",  # Mastercard 테스트
    "3782 822463 10005",  # Amex 테스트
    "6011 1111 1111 1117",  # Discover 테스트
)
# 사업자등록번호: 국세청 예제·공공기관 공개 번호(법인 식별용, 개인정보 아님).
_PUBLIC_BIZNO = (
    "220-81-62517",
    "101-81-25501",
    "104-81-45690",
    "124-81-00998",
)
_PUBLIC_SECRETS = (
    "AKIAIOSFODNN7EXAMPLE",  # AWS 문서 예제 (문서상 무효)
    "-----BEGIN PRIVATE KEY-----",
)

# ── 형식 규칙에서 손으로 유도 ──────────────────────────────────────────
# 무체크섬 4종. §2.1 이 "체크섬이 없어 형식만으로 판정한다" 고 적은 축이다.
_NO_CHECKSUM = (
    ("KR_PASSPORT", "M12345678"),
    ("KR_PASSPORT", "S87654321"),
    ("KR_PASSPORT", "R11223344"),
    ("KR_DL", "11-22-334455-66"),
    ("KR_DL", "28-01-987654-21"),
    ("KR_NHI", "1234567890"),
    ("KR_NHI", "2987654321"),
    ("KR_ACCOUNT", "110-234-567890"),
    ("KR_ACCOUNT", "352-1234-5678-93"),
)
# 국제 3종. 문서·표준이 예약한 값만 쓴다 — 실재 가입자 번호가 아니다.
_INTERNATIONAL = (
    ("PHONE", "010-1234-5678"),
    ("PHONE", "02-123-4567"),
    ("PHONE", "+82-10-9876-5432"),
    ("EMAIL", "user@example.test"),  # RFC 2606 예약 TLD
    ("EMAIL", "hong.gildong@example.com"),
    ("EMAIL", "admin+tag@example.org"),
)

# ── 자연어 표기 변형 ───────────────────────────────────────────────────
# §2.2 F4 "자연어로 완전히 풀어 쓴 식별자" 가 부분 탐지라고 적은 축이다.
_SPELLED_OUT = (
    ("PHONE", "공일공 일이삼사 오육칠팔"),
    ("PHONE", "영일영에 일이삼사 오육칠팔입니다"),
    ("EMAIL", "hong 골뱅이 example 점 test"),
)

_CARRIERS = (
    "{anchor}는 {value} 입니다.",
    "{anchor} {value} 확인 부탁드립니다.",
    "접수 내용: {anchor} {value}",
)
_ANCHOR = {
    "CARD": "결제 카드번호",
    "KR_BIZNO": "사업자등록번호",
    "KR_PASSPORT": "여권번호",
    "KR_DL": "운전면허번호",
    "KR_NHI": "건강보험증 번호",
    "KR_ACCOUNT": "입금 계좌",
    "PHONE": "연락처",
    "EMAIL": "이메일",
}
# §5.4 가 정한 칸 중 사람이 문장을 써야 하는 것. 채운 척하지 않고 규모만 남긴다.
UNFILLED_SLICES = {
    "third_generation_rrn": 125,
    "contextual_person_address": 100,
    "whitespace_variants": 100,
    "org_id": 50,
    "confidential_markers": 75 - len(_PUBLIC_SECRETS),
}


def _emit(index: int, label: str, value: str, provenance: str) -> CorpusRecord:
    carrier = _CARRIERS[index % len(_CARRIERS)]
    prefix = carrier.split("{value}")[0].format(anchor=_ANCHOR[label], value="")
    text = carrier.format(anchor=_ANCHOR[label], value=value)
    start = text.index(value, len(prefix) - 1 if prefix else 0)
    return CorpusRecord(
        id=f"t5-{provenance}-{index:04d}",
        task="pii",
        text=text,
        source="garim-t5",
        split="test",  # T5 는 평가 전용이다 — 학습에 넣지 않는다(§5.4).
        spans=[CorpusSpan(start=start, end=start + len(value), label=label)],
        template_id=f"t5-carrier-{index % len(_CARRIERS)}",
        meta={"provenance": provenance, "generator_free": True},
    )


def iter_t5_positives() -> Iterator[CorpusRecord]:
    """생성기를 거치지 않은 양성만 흘린다."""
    index = 0
    for value in _PUBLIC_CARDS:
        yield _emit(index, "CARD", value, "public-test-vector")
        index += 1
    for value in _PUBLIC_BIZNO:
        yield _emit(index, "KR_BIZNO", value, "public-test-vector")
        index += 1
    for label, value in _NO_CHECKSUM:
        yield _emit(index, label, value, "format-derived")
        index += 1
    for label, value in _INTERNATIONAL:
        yield _emit(index, label, value, "reserved-range")
        index += 1
    for label, value in _SPELLED_OUT:
        yield _emit(index, label, value, "spelled-out")
        index += 1


def generate_t5() -> list[CorpusRecord]:
    return list(iter_t5_positives())
