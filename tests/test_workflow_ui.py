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
