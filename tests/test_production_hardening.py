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
APPROVER = {"X-API-Key": "demo-approver-key"}
AUDITOR = {"X-API-Key": "demo-auditor-key"}


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


# ── P1: 대화-고객 바인딩 · 승인 TTL ──

def test_customer_binding_blocks_other_customer_send():
    """★confused-deputy★ — customer_id=A 로 바인딩하면 등록된 다른 고객 B로 발송이 차단된다."""
    # 고객 A(cust_00042)로 바인딩했는데 모델이 고객 B(cust_00043)의 이메일로 발송 시도.
    provider = FakeProvider.scripted([
        [("send_email", {"to": "seoyeon.lee@customer.example.com",   # 고객 B(등록됨)
                         "subject": "s", "body": "b"})],
        "완료",
    ])
    with TestClient(server.app, headers=AGENT) as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run",
                           json={"input": "메일 보내줘", "customer_id": "cust_00042"}).json()
        sent = [h for h in body["hops"] if h["tool"] == "send_email" and h["executed"]]
        assert not sent                                    # 타-고객 발송 차단
        assert any("authz" in r for r in body["blocked_rules"])


def test_customer_binding_unauthorized_customer_403():
    with TestClient(server.app, headers=AGENT) as client:
        server.set_provider(_send_email_provider())
        r = client.post("/v1/agent/run",
                        json={"input": "x", "customer_id": "cust_99999"})   # 미담당 고객
        assert r.status_code == 403


def test_approval_ttl_expiry_blocks_execution(monkeypatch):
    """만료된 승인은 실행되지 않는다(stale approval)."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "접수"])
    monkeypatch.setattr(server, "APPROVAL_TTL_S", 0.0)     # 즉시 만료
    with TestClient(server.app, headers=AGENT) as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        assert rr.status_code == 409 and "expired" in str(rr.json())
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 0
