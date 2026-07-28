"""실운영 하드닝 P0 — 프로덕 데모키 차단 · HTTP 요청 멱등성 · HITL 논리키 이중환불 차단.

외부 리뷰 P0 3건에 대한 회귀 고정.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from src.agent import server
from src.agent.authz import build_registry, DEMO_KEYS
from src.agent.backend import ServiceWorld
from src.agent.hitl import ApprovalQueue
from src.providers.fake import FakeProvider

AGENT = {"X-API-Key": "demo-agent-key"}


class _World:
    customers = [{"id": "cust_00042", "email": "minjun.kim@customer.example.com",
                  "account": "110-000-000001"}]


# ── P0-1: prod 데모 키 차단 ──

def test_prod_without_injected_creds_fails_boot():
    """APP_ENV=prod 인데 주입 자격증명 없으면 레지스트리 구성 실패(데모키 거부)."""
    old = os.environ.pop("SAFEAGENT_API_KEYS", None)
    try:
        with pytest.raises(RuntimeError, match="SAFEAGENT_API_KEYS"):
            build_registry(_World(), app_env="prod")
    finally:
        if old is not None:
            os.environ["SAFEAGENT_API_KEYS"] = old


def test_prod_with_injected_creds_has_no_demo_keys():
    keys = {"tenantA-agent": {"id": "agent:acme", "role": "agent",
                              "customer_ids": ["cust_00042"]}}
    os.environ["SAFEAGENT_API_KEYS"] = json.dumps(keys)
    try:
        reg = build_registry(_World(), app_env="prod")
        assert "tenantA-agent" in reg
        assert not any(k in reg for k in DEMO_KEYS)          # 데모 키 절대 없음
        p = reg["tenantA-agent"]
        assert p.customer_ids == frozenset({"cust_00042"})   # 테넌트/고객 스코프
        assert "minjun.kim@customer.example.com" in p.verified_emails
    finally:
        os.environ.pop("SAFEAGENT_API_KEYS", None)


def test_prod_rejects_demo_key_in_injected():
    os.environ["SAFEAGENT_API_KEYS"] = json.dumps({"demo-agent-key": {"role": "agent"}})
    try:
        with pytest.raises(RuntimeError, match="데모 키"):
            build_registry(_World(), app_env="prod")
    finally:
        os.environ.pop("SAFEAGENT_API_KEYS", None)


def test_nonprod_defaults_to_demo_keys():
    reg = build_registry(_World(), app_env="")
    assert set(DEMO_KEYS) <= set(reg)


# ── P0-3: HITL 논리적 멱등성 키 dedup ──

def test_hitl_submit_dedups_by_logical_key():
    q = ApprovalQueue()
    a1 = q.submit("issue_refund", {"account": "x", "amount": 100}, "ep1",
                  principal="agent:demo", idem_key="scope:1")
    a2 = q.submit("issue_refund", {"account": "x", "amount": 100}, "ep2",
                  principal="agent:demo", idem_key="scope:1")   # 같은 논리키 재시도
    assert a1 == a2                                   # 새 승인 생성 안 함
    assert len(q.list_pending()) == 1                 # 승인 한 건뿐


def test_hitl_submit_distinct_logical_keys_separate():
    q = ApprovalQueue()
    a1 = q.submit("issue_refund", {}, "ep1", principal="p", idem_key="scope:1")
    a2 = q.submit("issue_refund", {}, "ep1", principal="p", idem_key="scope:2")
    assert a1 != a2 and len(q.list_pending()) == 2


def test_backend_send_email_replay_returns_original():
    """같은 키로 다른 본문 재시도 → 저장된 원본 발송 결과 반환(재시도 인자 아님)."""
    w = ServiceWorld()
    w.send_email(to="a@customer.example.com", subject="원본", body="A", idempotency_key="k1")
    r = w.send_email(to="b@evil.example.org", subject="위조", body="B", idempotency_key="k1")
    assert r["idempotent_replay"] is True
    assert r["to"] == "a@customer.example.com" and r["subject"] == "원본"   # 원본, 재시도값 아님
    assert len(w.outbox) == 1


# ── P0-2: HTTP 요청 멱등성 ──

def _send_email_provider():
    return FakeProvider.scripted([
        [("send_email", {"to": "minjun.kim@customer.example.com",
                         "subject": "안내", "body": "내용"})],
        "발송 완료",
    ])


def test_http_idempotency_same_key_same_request_replays():
    with TestClient(server.app, headers=AGENT) as client:
        server.set_provider(_send_email_provider())
        h = {"Idempotency-Key": "req-1"}
        r1 = client.post("/v1/agent/run", json={"input": "고객에게 안내 메일 보내줘"}, headers=h)
        assert r1.status_code == 200
        n1 = client.get("/v1/audit", headers={"X-API-Key": "demo-auditor-key"}).json()["email_count"]
        # 같은 키·같은 요청 재시도 → 재실행 없이 원 응답, outbox 불변
        r2 = client.post("/v1/agent/run", json={"input": "고객에게 안내 메일 보내줘"}, headers=h)
        assert r2.status_code == 200
        assert r2.json()["episode_id"] == r1.json()["episode_id"]      # 원 응답 재생
        n2 = client.get("/v1/audit", headers={"X-API-Key": "demo-auditor-key"}).json()["email_count"]
        assert n2 == n1                                                # 부작용 이중 실행 없음


def test_http_idempotency_same_key_different_request_409():
    with TestClient(server.app, headers=AGENT) as client:
        server.set_provider(_send_email_provider())
        h = {"Idempotency-Key": "req-2"}
        r1 = client.post("/v1/agent/run", json={"input": "메일 A"}, headers=h)
        assert r1.status_code == 200
        r2 = client.post("/v1/agent/run", json={"input": "메일 B(다른 요청)"}, headers=h)
        assert r2.status_code == 409                                   # 같은 키/다른 요청
