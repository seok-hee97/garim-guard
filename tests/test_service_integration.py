"""서비스 통합 — HITL 승인 흐름, 상태 백엔드+idempotency, principal authz(confused-deputy).

실 API 없이 scripted FakeProvider 로 서버 엔드포인트 end-to-end 검증.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.agent import server
from src.agent.backend import service_world
from src.providers.fake import FakeProvider


def test_hitl_refund_approval_executes_and_is_idempotent():
    """★HITL 통합★ — issue_refund 는 즉시 실행되지 않고 hitl_pending → 사람 승인 후 실제 실행.
    재승인은 409(이중환불 없음)."""
    provider = FakeProvider.scripted([
        [("lookup_customer", {"phone": "010-1234-5678"})],
        [("issue_refund", {"account": "110-000-000001", "amount": 1000, "reason": "환불 요청"})],
        "환불 승인 요청을 접수했습니다.",
    ])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        r = client.post("/v1/agent/run", json={"input": "고객 환불 처리해 주세요"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "hitl_pending", body
        aid = body["pending_approval"]
        assert aid

        # 승인 전: 환불 미실행
        assert client.get("/v1/audit").json()["refund_count"] == 0
        pend = client.get("/v1/approvals").json()["pending"]
        target = next(a for a in pend if a["id"] == aid)
        assert target["tool"] == "issue_refund"
        assert "***" in target["args"]["account"]          # 계좌 마스킹(API 에 원본 노출 안 함)

        # 승인 → 실제 환불 실행
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve", "approver": "manager"})
        assert rr.status_code == 200, rr.text
        assert rr.json()["status"] == "approved"
        assert rr.json()["result"]["executed"] is True
        assert client.get("/v1/audit").json()["refund_count"] == 1

        # 재승인 → 409 (idempotent, 이중환불 없음)
        rr2 = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"})
        assert rr2.status_code == 409
        assert client.get("/v1/audit").json()["refund_count"] == 1


def test_hitl_deny_does_not_execute():
    """거부하면 환불이 실행되지 않는다."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 500, "reason": "테스트"})],
        "요청 접수",
    ])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "deny", "approver": "manager"})
        assert rr.json()["status"] == "denied"
        assert client.get("/v1/audit").json()["refund_count"] == 0


def test_confused_deputy_blocked_by_authz():
    """★confused-deputy 구조적 차단★ — allowlist 도메인 내 주소라도 담당 고객의 등록 이메일이
    아니면 authz 로 차단(taint 휴리스틱이 아니라 principal↔데이터주체 바인딩)."""
    provider = FakeProvider.scripted([
        [("send_email", {"to": "attacker@customer.example.com", "subject": "x", "body": "y"})],
        "완료",
    ])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run", json={"input": "이메일 보내줘"}).json()
        assert not any(h["tool"] == "send_email" and h["executed"] for h in body["hops"])
        assert any("authz.recipient_unverified" in r for r in body["blocked_rules"]), body


def test_backend_refund_idempotency_unit():
    """백엔드 단위 — 같은 idempotency_key 로 두 번 환불 시 이중 실행 안 됨."""
    w = service_world()
    r1 = w.issue_refund("110-000-000001", 1000, "x", idempotency_key="k1")
    r2 = w.issue_refund("110-000-000001", 1000, "x", idempotency_key="k1")
    assert r1["refund_id"] == r2["refund_id"]         # 동일 결과
    assert r2.get("idempotent_replay") is True
    assert len(w.refunds) == 1                         # 원장에 1건만


def test_send_email_idempotency_unit():
    """백엔드 단위 — 같은 key 로 두 번 발송 시 outbox 1건(중복발송 방지)."""
    w = service_world()
    w.send_email("a@customer.example.com", "s", "b", idempotency_key="e1")
    r2 = w.send_email("a@customer.example.com", "s", "b", idempotency_key="e1")
    assert r2.get("idempotent_replay") is True
    assert len(w.outbox) == 1


def test_no_api_key_is_401():
    """★fail-closed authz★ — X-API-Key 없으면 401(무인증 전-고객 접근 차단)."""
    with TestClient(server.app) as client:      # 헤더 없음
        server.set_provider(FakeProvider.scripted(["x"]))
        r = client.post("/v1/agent/run", json={"input": "환불"})
        assert r.status_code == 401


def test_audit_masks_account():
    """★/v1/audit PII 마스킹★ — 처리된 환불의 계좌가 마스킹되어 노출된다."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        client.post(f"/v1/approvals/{aid}", json={"decision": "approve"})
        refunds = client.get("/v1/audit").json()["refunds"]
        assert refunds and "***" in refunds[0]["account"]          # 마스킹됨
        assert "110-000-000001" not in str(refunds)                # 원본 계좌 미노출


def test_approval_executor_failure_keeps_pending_retryable():
    """★HITL 트랜잭션성★ — 승인 시 백엔드 실패면 승인이 굳지 않고(재시도 가능) 환불 유실 없음."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        world = server._state["world"]
        orig = world.issue_refund
        world.issue_refund = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Stripe 5xx"))
        r = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"})
        assert r.status_code == 502                                 # 재시도 가능 오류
        assert client.get("/v1/audit").json()["refund_count"] == 0  # 환불 미실행(유실 아님)
        # 백엔드 복구 후 재승인 → 성공(승인이 approved 로 굳지 않았음)
        world.issue_refund = orig
        r2 = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"})
        assert r2.status_code == 200 and r2.json()["status"] == "approved"
        assert client.get("/v1/audit").json()["refund_count"] == 1


def test_send_email_cross_episode_not_deduped():
    """★idempotency 키 회귀★ — 서로 다른 에피소드의 다른 이메일이 모델 call_id 충돌로 유실되면 안 됨."""
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(FakeProvider.scripted(
            [[("send_email", {"to": "minjun.kim@customer.example.com", "subject": "A", "body": "x"})], "ok"]))
        client.post("/v1/agent/run", json={"input": "A"})
        server.set_provider(FakeProvider.scripted(
            [[("send_email", {"to": "seoyeon.lee@customer.example.com", "subject": "B", "body": "y"})], "ok"]))
        client.post("/v1/agent/run", json={"input": "B"})
        assert client.get("/v1/audit").json()["email_count"] == 2   # 둘 다 발송(유실 없음)


def test_unauthenticated_approvals_and_audit_blocked():
    """★무인증 차단★ — /v1/approvals·/v1/audit 도 인증 없으면 401(비가역 실행·PII 노출 방지)."""
    with TestClient(server.app) as client:                          # 헤더 없음
        server.set_provider(FakeProvider.scripted(["x"]))
        assert client.get("/v1/approvals").status_code == 401
        assert client.get("/v1/audit").status_code == 401
        assert client.post("/v1/approvals/appr_00001", json={"decision": "approve"}).status_code == 401


def test_approval_result_masks_account():
    """★result 마스킹★ — 승인 응답의 backend result 에도 원본 계좌가 노출되지 않는다."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "t"})], "ok"])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        aid = client.post("/v1/agent/run", json={"input": "환불"}).json()["pending_approval"]
        rr = client.post(f"/v1/approvals/{aid}", json={"decision": "approve"})
        assert "110-000-000001" not in rr.text          # 원본 계좌 미노출
        assert rr.json()["result"]["executed"] is True


def test_parallel_hitl_batch_no_loss():
    """★병렬 HITL★ — 한 턴에 비가역 도구 2개면 둘 다 승인 큐에 적재(유실 없음)."""
    provider = FakeProvider.scripted([
        [("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "a"}),
         ("issue_refund", {"account": "110-000-000002", "amount": 200, "reason": "b"})],
        "요청 접수",
    ])
    with TestClient(server.app, headers={"X-API-Key": "demo-agent-key"}) as client:
        server.set_provider(provider)
        body = client.post("/v1/agent/run", json={"input": "환불 2건"}).json()
        assert body["status"] == "hitl_pending"
        assert len(client.get("/v1/approvals").json()["pending"]) == 2   # 2건 모두 적재
