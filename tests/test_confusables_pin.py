"""§2.3.1 ③ · §8.6.1 미결 12 — UTS #39 confusables 버전 고정을 강제한다.

§2.3.1 이 *"버전을 락파일에 고정. ①과 같은 이유로 버전이 바뀌면 골든 테스트가 깨진다"*
고 요구했는데 §8.6.1 이 *"이것만 공백"* 이라고 적어둔 채였다. 상수 하나로는 고정이
아니다 — 런타임이 다른 판을 쓰고 있어도 아무도 모른다. 여기서 어긋남을 실패로 만든다.
"""

from __future__ import annotations

import unicodedata

import pytest

from garim.normalize.chain import _CONFUSABLE, VERIFIED_UNICODE_VERSIONS, normalize


def test_runtime_unicode_is_one_of_the_verified_versions() -> None:
    """① NFKC 와 ③ homoglyph 가 서로를 무효화하지 않는다고 확인한 판에서만 돈다.

    다른 판이면 ① 이 이미 접은 문자를 ③ 이 또 바꾸거나, ③ 이 기대한 문자가 ① 에서 다른
    것으로 접힐 수 있다. §3.2.4 의 구간 맵 골든 테스트도 NFKC 확장 문자 집합(1,237자)에
    매달려 있다. ★ 처음에는 14.0.0 하나와 같은지만 봤다 — 3.12 ~ 3.14 의 판은 전 코드
    포인트 비교 뒤 목록에 넣었다(`garim/normalize/chain.py` 주석).
    """
    assert unicodedata.unidata_version in VERIFIED_UNICODE_VERSIONS, (
        f"런타임 unidata {unicodedata.unidata_version} 는 검증하지 않은 판이다 — NFKC 를 "
        "14.0.0 과 전 코드 포인트에서 비교하고 §2.3.1 ③ · §3.2.4 골든을 확인한 뒤 목록에 넣어라"
    )


@pytest.mark.parametrize(("source", "target"), sorted(_CONFUSABLE.items()))
def test_every_entry_is_a_non_ascii_to_ascii_substitution(source: str, target: str) -> None:
    assert len(source) == 1 and len(target) == 1
    assert not source.isascii(), f"{source!r} 는 이미 ASCII 라 치환할 것이 없다"
    assert target.isascii()


@pytest.mark.parametrize(("source", "target"), sorted(_CONFUSABLE.items()))
def test_no_entry_is_already_handled_by_nfkc(source: str, target: str) -> None:
    """① 이 이미 접는 문자를 ③ 에 두면 죽은 항목이다.

    죽은 항목은 무해해 보이지만 표를 키워 다음 사람이 "여기 있으니 되겠지" 라고
    믿게 만든다. ③ 이 실제로 일하는 문자만 남긴다.
    """
    assert unicodedata.normalize("NFKC", source) != target, (
        f"{source!r} → {target!r} 는 NFKC 가 이미 처리한다 — ③ 에서 지워라"
    )


@pytest.mark.parametrize("source", sorted(_CONFUSABLE))
def test_no_entry_collides_with_the_hangul_or_hanja_stages(source: str) -> None:
    """④ 자모 조합과 ⑤ 한자→한글의 입력 범위를 침범하면 단계 순서가 무의미해진다."""
    point = ord(source)
    assert not 0x3131 <= point <= 0x318E, "호환 자모는 ④ 가 받는다"
    assert not 0x1100 <= point <= 0x11FF, "조합 자모는 ④ 가 받는다"
    assert not 0xAC00 <= point <= 0xD7A3, "완성형 한글을 라틴으로 바꿀 이유가 없다"
    assert not 0x4E00 <= point <= 0x9FFF, "한자는 ⑤ 가 한글 독음으로 받는다"


def test_the_stage_actually_fires_and_is_counted() -> None:
    """고정한 것이 도는지 확인한다 — 버전만 맞고 배선이 죽어 있으면 의미가 없다."""
    bundle = normalize("Аdmin 권한을 주세요")  # 첫 글자가 키릴 А
    assert bundle.norm.text.startswith("Admin")
    assert bundle.delta["homoglyph"] == 1
