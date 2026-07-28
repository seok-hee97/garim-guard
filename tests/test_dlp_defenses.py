"""신규 방어/DLP 컴포넌트 테스트 — plea·spotlight·tier_c 가드·PresidioVault."""

from __future__ import annotations

import pytest

from src.config import load_policy
from src.agent.policy import DefenseHooks
from src.providers.base import Msg, ToolResult


def _pol():
    return load_policy()


# ── theater 기준선이 실제로 프롬프트를 바꾸나 (no-op 아님) ──

@pytest.mark.asyncio
async def test_plea_prepends_instruction():
    h = DefenseHooks(_pol(), {"plea": True})
    r = await h.before_model([Msg(role="user", text="환불 처리해줘")])
    assert r is not None
    assert "따르지 마세요" in r.messages[0].text and "환불 처리해줘" in r.messages[0].text


@pytest.mark.asyncio
async def test_spotlight_wraps_untrusted_output():
    h = DefenseHooks(_pol(), {"spotlight": True})
    w = await h.after_tool(ToolResult(call_id="search_kb#0", name="search_kb", content="오염 문서"))
    assert "UNTRUSTED_DATA" in str(w.output) and "오염 문서" in str(w.output)


@pytest.mark.asyncio
async def test_spotlight_only_marks_untrusted_tools():
    """신뢰 도구(lookup_customer) 출력은 감싸지 않는다."""
    h = DefenseHooks(_pol(), {"spotlight": True})
    w = await h.after_tool(ToolResult(call_id="lookup_customer#0", name="lookup_customer",
                                      content="고객 정보"))
    assert "UNTRUSTED_DATA" not in str(w.output)


def test_tier_c_raises_not_silent_noop():
    """미구현 Tier C 는 조용한 no-op 이 아니라 명시적 에러 (결과 오염 방지)."""
    with pytest.raises(NotImplementedError, match="Tier C"):
        DefenseHooks(_pol(), {"tier_c": {"enable": True}})


# ── PresidioVault 결함 패치 ──

def test_presidio_passport_defect_and_patch():
    pytest.importorskip("presidio_analyzer")   # core 설치(presidio 미포함)에선 ERROR 아닌 SKIP
    from src.dlp.vault_presidio import _load_recognizers
    unpatched = {r.__class__.__name__: getattr(r, "supported_language", "?")
                 for r in _load_recognizers(patch_defects=False)}
    patched = {r.__class__.__name__: getattr(r, "supported_language", "?")
               for r in _load_recognizers(patch_defects=True)}
    # 결함 확인: 미패치 여권은 'kr'
    passport = [k for k in unpatched if "Passport" in k]
    assert passport, "여권 인식기 존재"
    assert unpatched[passport[0]] == "kr"   # 결함
    assert patched[passport[0]] == "ko"     # 패치


def test_presidio_vault_roundtrip():
    pytest.importorskip("presidio_analyzer")   # core 설치(presidio 미포함)에선 ERROR 아닌 SKIP
    from src.dlp.vault_presidio import PresidioVault
    v = PresidioVault()
    t = v.redact("주민등록번호 900101-1234561 여권 M12345678")
    assert "<KR_RRN_1>" in t
    assert "<KR_PASSPORT_1>" in t          # 패치 덕에 여권도 탐지
    assert v.restore(t) == "주민등록번호 900101-1234561 여권 M12345678"
