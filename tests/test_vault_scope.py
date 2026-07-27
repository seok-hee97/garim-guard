"""PIIVault 수명 테스트 — 요청당 정확히 1개 인스턴스의 두 실패모드를 봉인한다.

  ❌ fresh-per-call  → coreference 붕괴 (같은 사람이 홉마다 다른 토큰)
  ❌ global singleton → 요청 간 PII 누출 (요청 A 매핑이 요청 B 에 노출)
"""

from __future__ import annotations

from src.dlp.vault import RegexVault, new_vault


def test_hop_consistency_within_request():
    """한 요청(=1 vault) 안에서 같은 값은 항상 같은 토큰 (coreference 유지)."""
    v = new_vault()
    a = v.redact("전화 010-1234-5678 주민 900101-1234561")
    b = v.redact("다시 010-1234-5678 확인, 주민 900101-1234561")
    assert "<KR_PHONE_1>" in a and "<KR_PHONE_1>" in b
    assert "<KR_RRN_1>" in a and "<KR_RRN_1>" in b


def test_roundtrip_exact():
    v = new_vault()
    original = "김민준 010-1234-5678 900101-1234561 minjun@customer.example.com"
    red = v.redact(original)
    assert v.restore(red) == original


def test_fresh_per_call_breaks_coreference():
    """fresh-per-call 금지 근거: vault 를 새로 만들면 coreference 가 깨진다 (반례 시연)."""
    # 잘못된 방식: 홉마다 새 vault
    v1, v2 = new_vault(), new_vault()
    t1 = v1.redact("010-1234-5678")
    t2 = v2.redact("010-9999-8888")   # 다른 사람인데
    # 둘 다 <KR_PHONE_1> → 서로 다른 사람이 같은 토큰 = coreference 붕괴
    assert t1 == t2 == "<KR_PHONE_1>"
    # 요청 스코프 1개였다면 서로 다른 토큰이어야 한다:
    v = new_vault()
    assert v.redact("010-1234-5678") == "<KR_PHONE_1>"
    assert v.redact("010-9999-8888") == "<KR_PHONE_2>"   # 다른 토큰


def test_no_cross_request_leak():
    """global singleton 금지 근거: 요청마다 새 vault → 요청 간 매핑 격리."""
    va = new_vault(); va.redact("900101-1234561")
    vb = new_vault()
    # 요청 B 의 vault 는 요청 A 의 값을 모른다
    assert vb._t2v == {}
    assert "900101-1234561" not in vb._v2t


def test_sink_leak_detection():
    """싱크별 유출 계측 — 원본 PII 가 텍스트에 노출됐는지."""
    v = new_vault()
    v.redact("주민 900101-1234561")
    assert v.contains_raw("메일에 900101-1234561 포함") == ["900101-1234561"]
    assert v.contains_raw("메일에 <KR_RRN_1> 포함(토큰)") == []


def test_isolated_scope_type():
    assert isinstance(new_vault(), RegexVault)
