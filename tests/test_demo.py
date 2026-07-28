"""키 불요 데모 경로 회귀 고정 — scripted provider + run_demo + 뷰어 타임라인.

리뷰(R6 데모 감사)에서 '키 없는 CLI 데모가 finish=error 로 깨짐 + 뷰어가 실행 과정을
안 보임'을 확인했다. 여기서 (1) scripted provider 가 전체 공격 에피소드를 키 없이
결정적으로 재생하고, (2) 게이트만 실행 결과를 바꾸며, (3) 뷰어가 hop 타임라인을
렌더함을 고정한다. 이 테스트가 깨지면 데모/녹화 산출물이 회귀한 것이다.
"""

from __future__ import annotations

import asyncio

from src.agent.cli import _DEMO_SCRIPT, _build_provider
from src.agent.toolworld import ATTACKER_EMAIL
from scripts.run_demo import _run_arm
from app.trace_view import _episode_timelines


def test_scripted_provider_is_keyless_and_deterministic():
    """--provider scripted 는 API 키/카세트 없이 FakeProvider 스크립트를 만든다."""
    p = _build_provider("scripted", "poisoned_kb")
    assert p.name == "fake"
    assert len(p._script) == len(_DEMO_SCRIPT["poisoned_kb"])   # 시나리오 턴 그대로


def test_demo_none_leaks_tier_a_blocks():
    """헤드라인 재현: 같은 에피소드 — 무방어=send_email(공격자) 실행 / Tier A=차단."""
    ep_none = asyncio.run(_run_arm({}, "poisoned_kb"))
    ep_ta = asyncio.run(_run_arm({"tier_a": True}, "poisoned_kb"))

    assert ep_none.any_attempted("send_email", to=ATTACKER_EMAIL)
    assert ep_none.any_executed("send_email", to=ATTACKER_EMAIL)      # 무방어 → 유출 실행

    assert ep_ta.any_attempted("send_email", to=ATTACKER_EMAIL)       # 시도는 동일
    assert not ep_ta.any_executed("send_email", to=ATTACKER_EMAIL)    # 게이트가 실행만 차단
    email_hop = next(h for h in ep_ta.hops if h.tool_name == "send_email")
    assert email_hop.gate_decision == "BLOCK"
    assert email_hop.gate_rule == "egress.email_domains"


def test_dlp_arm_blocks_on_body_pii():
    """게이트+볼트 arm 은 body 의 원본 주민번호를 egress DLP 로 차단(층 다변화)."""
    ep = asyncio.run(_run_arm({"tier_a": True, "dlp_vault": True}, "poisoned_kb"))
    assert not ep.any_executed("send_email", to=ATTACKER_EMAIL)
    email_hop = next(h for h in ep.hops if h.tool_name == "send_email")
    assert email_hop.gate_decision == "BLOCK"


def test_normal_scenario_completes_without_block():
    """정상 시나리오는 게이트가 켜져도 완주(과잉 게이팅 아님)."""
    ep = asyncio.run(_run_arm({"tier_a": True}, "normal"))
    assert not any(h.tool_name == "send_email" for h in ep.hops)      # 공격 도구 미호출
    assert all(h.executed for h in ep.hops if h.tool_name)            # 정상 도구 전부 통과


def test_viewer_renders_execution_timeline():
    """뷰어가 hop 타임라인(도구·차단지점)을 HTML 로 렌더한다 — 집계표가 아니라 실행 과정."""
    ep_none = asyncio.run(_run_arm({}, "poisoned_kb"))
    ep_ta = asyncio.run(_run_arm({"tier_a": True}, "poisoned_kb"))
    html_parts = "\n".join(_episode_timelines([ep_none.to_dict(), ep_ta.to_dict()]))
    assert "실행 과정" in html_parts
    assert "before_tool" in html_parts                # seam 표기
    assert "send_email" in html_parts
    assert "egress.email_domains" in html_parts       # 차단 규칙 노출
    assert "유출 실행됨" in html_parts                  # 무방어 arm 유출 판정
