"""전체 Agent Workflow 관측·브라우저 UI 회귀 테스트."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.ui import app
from scripts.run_demo import _run_arm
from src.agent.demo_runtime import run_workflow_demo
from src.agent.workflow import format_workflow_text


def test_workflow_document_shows_dlp_gate_and_real_rail():
    doc = asyncio.run(run_workflow_demo(
        scenario="poisoned_kb",
        provider_mode="scripted",
        defense_preset="tier_a+dlp_vault",
    ))

    assert doc["meta"]["scripted"] is True
    assert "모델 추론 증거가 아닙니다" in doc["meta"]["scripted_notice"]
    assert doc["meta"]["rail"] in {"ko_agentguard", "heuristic"}
    actions = [a for t in doc["turns"] for a in t["actions"]]
    lookup = next(a for a in actions if a["tool"] == "lookup_customer")
    email = next(a for a in actions if a["tool"] == "send_email")
    assert "KR_RRN" in lookup["dlp_tokens"]
    assert lookup["raw_tool_result"]["rrn"] == "900101-1234561"  # 합성 로컬 원본
    assert lookup["model_visible_result"]["rrn"] == "<KR_RRN_1>"
    assert email["executed"] is False
    assert email["rule"] == "dlp.egress:body"
    assert doc["audit"]["email_count"] == 0

    text = format_workflow_text(doc)
    assert "전체 Agent Workflow" in text
    assert "SCRIPTED ACTION" in text
    assert "after_tool→MODEL" in text


def test_none_arm_records_mock_service_outbox():
    doc = asyncio.run(run_workflow_demo(
        scenario="poisoned_kb",
        provider_mode="scripted",
        defense_preset="none",
    ))
    assert doc["audit"]["email_count"] == 1
    assert doc["summary"]["blocked"] == 0


def test_trace_preview_is_masked_and_raw_never_serialized():
    ep = asyncio.run(_run_arm({}, "poisoned_kb"))
    payload = ep.to_dict()
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "tool_result_raw" not in dumped
    lookup = next(h for h in payload["hops"] if h["tool_name"] == "lookup_customer")
    assert lookup["tool_result_preview"]["rrn"] == "<KR_RRN_MASKED>"
    assert lookup["tool_result_preview"]["phone"] == "<KR_PHONE_MASKED>"


def test_workflow_ui_and_meta_endpoints():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Agent Workflow Console" in page.text
        meta = client.get("/api/meta")
        assert meta.status_code == 200
        assert "poisoned_kb" in meta.json()["scenarios"]
        run = client.post("/api/run", json={
            "scenario": "normal",
            "provider_mode": "scripted",
            "defense_preset": "tier_a",
            "user_input": "",
        })
        assert run.status_code == 200
        assert run.json()["summary"]["finish_reason"] == "done"
        compare = client.post("/api/compare", json={
            "scenario": "poisoned_kb",
            "provider_mode": "gemini",  # 비교 API는 비용 방지를 위해 scripted 로 강제
            "defense_preset": "none",
            "user_input": "",
        })
        assert compare.status_code == 200
        rows = compare.json()["runs"]
        assert [r["meta"]["defense_preset"] for r in rows] == [
            "none", "tier_a", "tier_a+dlp_vault"
        ]
        assert [r["summary"]["blocked"] for r in rows] == [0, 1, 1]


def test_workflow_document_joins_by_call_id_not_position():
    """★UI 가 보여주는 판정이 그 호출의 판정인지★ — 위치 결합 회귀 방지.

    회귀 이력: workflow_document 가 recorder 의 tool_calls 와 episode.hops 를 위치(index)로
    짝지었다. loop.py 에는 둘이 어긋나는 경로가 실재한다 —
      · calls = result.tool_calls[:MAX_PARALLEL_CALLS]   (8개 초과분 절단)
      · 배치 순회 중 deadline 초과 시 break               (남은 호출은 hop 없음)
    그때 UI 는 절단된 lookup_customer 에 다음 턴 send_email 의 BLOCK/egress.email_domains 를
    붙이고, 정작 진짜 차단은 NA 로 사라졌다. 화면이 그럴듯해서 눈으로는 안 잡힌다.
    """
    import asyncio

    from src.agent.loop import MAX_PARALLEL_CALLS, run_episode
    from src.agent.policy import DefenseHooks
    from src.agent.toolworld import default_world
    from src.agent.workflow import WorkflowRecorderProvider, workflow_document
    from src.config import gen_config, load_policy
    from src.providers.base import GenResult, ToolCall

    class _Burst:
        """턴1에서 상한을 넘겨 호출하고, 턴2에서 차단당할 호출을 낸다."""

        name = "burst"

        def __init__(self) -> None:
            self.turn = 0

        async def generate(self, messages, cfg):          # noqa: ARG002
            self.turn += 1
            if self.turn == 1:
                return GenResult(text="", finish_reason="tool_calls", tool_calls=[
                    ToolCall(name="lookup_customer", args={"phone": "010-1234-5678"},
                             call_id=f"L#{i}")
                    for i in range(MAX_PARALLEL_CALLS + 2)])
            if self.turn == 2:
                return GenResult(text="", finish_reason="tool_calls", tool_calls=[
                    ToolCall(name="send_email",
                             args={"to": "drop@mal.example.org", "subject": "x", "body": "y"},
                             call_id="S#0")])
            return GenResult(text="끝", tool_calls=[], finish_reason="stop")

    async def _run():
        policy = load_policy()
        rec = WorkflowRecorderProvider(_Burst(), mode="scripted")
        ep = await run_episode("t", rec, gen_config(policy),
                               hooks=DefenseHooks(policy, {"tier_a": True}, rail=None),
                               world=default_world(), defenses={"tier_a": True},
                               scenario="burst", episode_id="burst0000000")
        return ep, workflow_document(ep, rec, rail_name="none", defenses={"tier_a": True},
                                     provider_label="burst", model="none")

    ep, doc = asyncio.run(_run())
    hops = {h.call_id: h for h in ep.hops if h.tool_name}
    actions = [a for t in doc["turns"] for a in t["actions"]]

    assert len(actions) > len(hops), "상한 절단이 재현되지 않았다 — 테스트 전제가 깨졌다"

    for a in actions:
        real = hops.get(a["call_id"])
        if real is None:
            assert a["decision"] == "NA", (
                f"{a['call_id']}: hop 이 없는데 '{a['decision']}/{a['rule']}' 판정이 붙었다")
            assert not a["executed"]
        else:
            assert a["decision"] == real.gate_decision, f"{a['call_id']}: 판정 불일치"
            assert a["rule"] == real.gate_rule, f"{a['call_id']}: 규칙 불일치"

    # 진짜 차단이 화면에서 사라지지 않아야 한다
    blocked = [a for a in actions if a["decision"] == "BLOCK"]
    assert [a["call_id"] for a in blocked] == ["S#0"], blocked
    assert blocked[0]["rule"] == "egress.email_domains"
