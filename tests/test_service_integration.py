"""서비스 통합 — HITL 승인 흐름(역할분리), 상태 백엔드+idempotency, principal authz.

실 API 없이 scripted FakeProvider 로 서버 엔드포인트 end-to-end 검증.
역할분리: 에이전트 실행=demo-agent-key, 승인=demo-approver-key, 감사=demo-auditor-key.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.agent import server
from src.agent.backend import service_world
from src.providers.fake import FakeProvider

AGENT = {"X-API-Key": "demo-agent-key"}
APPROVER = {"X-API-Key": "demo-approver-key"}
AUDITOR = {"X-API-Key": "demo-auditor-key"}


def _client():
    return TestClient(server.app, headers=AGENT)      # 기본 헤더=agent(실행). 승인/감사는 per-request 오버라이드


def test_hitl_refund_approval_executes_and_is_idempotent():
    """★HITL 통합★ — issue_refund 는 hitl_pending → 승인자 승인 후 실행. 재승인 409."""
    provider = FakeProvider.scripted([
        [("lookup_customer", {"phone": "010-1234-5678"})],
        [("issue_refund", {"account": "110-000-000001", "amount": 1000, "reason": "환불 요청"})],
        "환불 승인 요청을 접수했습니다.",
    ])
    with _client() as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run", json={"input": "고객 환불 처리해 주세요"}).json()
        assert body["status"] == "hitl_pending", body
        aid = body["pending_approval"]

        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 0
        pend = client.get("/v1/approvals", headers=APPROVER).json()["pending"]
        target = next(a for a in pend if a["id"] == aid)
        assert target["tool"] == "issue_refund" and "***" in target["args"]["account"]

        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        assert rr.status_code == 200, rr.text
        assert rr.json()["status"] == "approved" and rr.json()["result"]["executed"] is True
        assert rr.json()["approver"] == "approver:demo"          # 토큰에서 결정(클라이언트 문자열 아님)
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 1

        rr2 = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        assert rr2.status_code == 409
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 1


def test_agent_cannot_self_approve():
    """★직무분리★ — 에이전트 키(요청자)는 자기 요청을 승인할 수 없다(403)."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        # 에이전트 키로 승인 시도 → 역할 불일치 403
        assert client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=AGENT).status_code == 403
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 0


def test_hitl_deny_does_not_execute():
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 500, "reason": "테스트"})], "요청 접수"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "deny"}, headers=APPROVER)
        assert rr.json()["status"] == "denied"
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 0


def test_confused_deputy_blocked_by_authz():
    """confused-deputy — allowlist 도메인 내라도 담당 고객 등록 이메일 아니면 authz 차단."""
    provider = FakeProvider.scripted([
        [("send_email", {"to": "attacker@customer.example.com", "subject": "x", "body": "y"})], "완료"])
    with _client() as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run", json={"input": "이메일 보내줘"}).json()
        assert not any(h["tool"] == "send_email" and h["executed"] for h in body["hops"])
        assert any("authz.recipient_unverified" in r for r in body["blocked_rules"]), body


def test_backend_refund_idempotency_unit():
    w = service_world()
    r1 = w.issue_refund("110-000-000001", 1000, "x", idempotency_key="k1")
    r2 = w.issue_refund("110-000-000001", 1000, "x", idempotency_key="k1")
    assert r1["refund_id"] == r2["refund_id"] and r2.get("idempotent_replay") is True
    assert len(w.refunds) == 1


def test_send_email_idempotency_unit():
    w = service_world()
    w.send_email("a@customer.example.com", "s", "b", idempotency_key="e1")
    r2 = w.send_email("a@customer.example.com", "s", "b", idempotency_key="e1")
    assert r2.get("idempotent_replay") is True and len(w.outbox) == 1


def test_no_api_key_is_401():
    with TestClient(server.app) as client:
        server.set_provider(FakeProvider.scripted(["x"]))
        assert client.post("/v1/agent/run", json={"input": "환불"}).status_code == 401


def test_wrong_role_is_403():
    """감사 키로 에이전트 실행 시도 → 역할 불일치 403."""
    with TestClient(server.app) as client:
        server.set_provider(FakeProvider.scripted(["x"]))
        assert client.post("/v1/agent/run", json={"input": "x"}, headers=AUDITOR).status_code == 403


def test_audit_masks_account():
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        refunds = client.get("/v1/audit", headers=AUDITOR).json()["refunds"]
        assert refunds and "***" in refunds[0]["account"]
        assert "110-000-000001" not in str(refunds)


def test_approval_args_reason_pii_masked():
    """★args.reason PII 마스킹★ — 승인 목록의 사유에 원본 PII(전화·이메일)가 노출되지 않는다."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100,
                           "reason": "고객 010-1234-5678 minjun@x.com 환불"})], "ok"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        pend = client.get("/v1/approvals", headers=APPROVER)
        assert "010-1234-5678" not in pend.text and "minjun@x.com" not in pend.text


def test_approval_executor_failure_keeps_pending_retryable():
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        world = server._state["world"]
        orig = world.issue_refund
        world.issue_refund = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Stripe 5xx"))
        assert client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER).status_code == 502
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 0
        world.issue_refund = orig
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        assert rr.status_code == 200 and rr.json()["status"] == "approved"
        assert client.get("/v1/audit", headers=AUDITOR).json()["refund_count"] == 1


def test_send_email_cross_episode_not_deduped():
    """서로 다른 에피소드의 다른 이메일이 유실되면 안 됨."""
    with _client() as client:
        server.set_provider(FakeProvider.scripted(
            [[("send_email", {"to": "minjun.kim@customer.example.com", "subject": "A", "body": "x"})], "ok"]))
        client.post("/v1/agent/run", json={"input": "A"})
        server.set_provider(FakeProvider.scripted(
            [[("send_email", {"to": "seoyeon.lee@customer.example.com", "subject": "B", "body": "y"})], "ok"]))
        client.post("/v1/agent/run", json={"input": "B"})
        assert client.get("/v1/audit", headers=AUDITOR).json()["email_count"] == 2


def test_send_email_multiturn_same_episode_not_deduped():
    """★멱등성 ordinal 회귀★ — 한 에피소드 2턴의 다른 이메일이 call_id 리셋으로 유실되면 안 됨."""
    provider = FakeProvider.scripted([
        [("send_email", {"to": "minjun.kim@customer.example.com", "subject": "A", "body": "x"})],
        [("send_email", {"to": "seoyeon.lee@customer.example.com", "subject": "B", "body": "y"})],
        "완료",
    ])
    with _client() as client:
        server.set_provider(provider)
        client.post("/v1/agent/run", json={"input": "둘 다 발송"})
        assert client.get("/v1/audit", headers=AUDITOR).json()["email_count"] == 2   # 둘 다 발송


def test_unauthenticated_approvals_and_audit_blocked():
    with TestClient(server.app) as client:
        server.set_provider(FakeProvider.scripted(["x"]))
        assert client.get("/v1/approvals").status_code == 401
        assert client.get("/v1/audit").status_code == 401
        assert client.post("/v1/approvals/appr_00001", json={"decision": "approve"}).status_code == 401


def test_approval_result_masks_account():
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with _client() as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"}, headers=APPROVER)
        assert "110-000-000001" not in rr.text and rr.json()["result"]["executed"] is True


def test_parallel_hitl_batch_no_loss():
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "a"}),
         ("issue_refund", {"account": "110-000-000002", "amount": 200, "reason": "b"})],
        "요청 접수",
    ])
    with _client() as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run", json={"input": "환불 2건"}).json()
        assert body["status"] == "hitl_pending"
        assert len(client.get("/v1/approvals", headers=APPROVER).json()["pending"]) == 2
