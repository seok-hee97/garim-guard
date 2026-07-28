"""멀티테넌트 하드닝 회귀 — P0-1..5 + imp-1/3 (외부 리뷰 2차).

각 테스트는 리뷰가 재현한 공격이 이제 차단됨을 고정한다.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from src.agent import server
from src.agent.authz import Principal, build_registry
from src.agent.backend import ServiceWorld
from src.agent.hitl import ApprovalQueue
from src.agent.hooks import Block, HITLRequired
from src.agent.policy import DefenseHooks
from src.config import load_policy
from src.providers.base import ToolCall
from src.providers.fake import FakeProvider


def _bound(cust_id, phone, email, acct, rrn, role="agent"):
    return Principal(id=f"{role}:{cust_id}", role=role, customer_ids=frozenset({cust_id}),
                     verified_emails=frozenset({email}), verified_accounts=frozenset({acct}),
                     verified_phones=frozenset({phone}), verified_rrns=frozenset({rrn}))


# ── P0-3: 환불 reason 원본 PII 는 HITL 앞에서 차단 ──

def test_p0_3_refund_reason_pii_blocked_before_hitl():
    pol = load_policy()
    p = _bound("cust_00042", "010-1234-5678", "minjun.kim@customer.example.com",
               "110-000-000001", "900101-1234561")
    h = DefenseHooks(pol, {"tier_a": True, "dlp_vault": True}, principal=p)
    h.vault.redact("고객 010-9999-8888 문의")     # 볼트에 원본 전화 매핑
    call = ToolCall(name="issue_refund",
                    args={"account": "<KR_ACCOUNT_1>", "amount": 1000, "reason": "고객 010-9999-8888 환불"},
                    call_id="issue_refund#0")
    r = asyncio.run(h.before_tool(call, ctx=None))
    assert isinstance(r, Block) and r.rule.startswith("dlp.egress")   # HITL 아니라 DLP 차단
    assert not isinstance(r, HITLRequired)


# ── P0-4: 바인딩 고객 밖 조회 차단 ──

def test_p0_4_lookup_out_of_scope_blocked():
    pol = load_policy()
    p = _bound("cust_00042", "010-1234-5678", "minjun.kim@customer.example.com",
               "110-000-000001", "900101-1234561")
    h = DefenseHooks(pol, {"tier_a": True, "dlp_vault": True}, principal=p)
    out = asyncio.run(h.before_tool(ToolCall(name="lookup_customer",
                                             args={"phone": "010-9999-8888"}, call_id="lookup#0"), ctx=None))
    assert isinstance(out, Block) and out.rule == "authz.lookup_out_of_scope"
    ok = asyncio.run(h.before_tool(ToolCall(name="lookup_customer",
                                            args={"phone": "010-1234-5678"}, call_id="lookup#1"), ctx=None))
    assert not (isinstance(ok, Block) and getattr(ok, "rule", "") == "authz.lookup_out_of_scope")


# ── P0-2: HITL 승인 목록이 테넌트 scope 로 필터 ──

def test_p0_2_hitl_list_scoped_by_customer():
    q = ApprovalQueue()
    q.submit("issue_refund", {"account": "a", "amount": 1}, "ep", principal="agent:t42",
             idem_key="k", customer_id="cust_00042")
    assert len(q.list_pending(frozenset({"cust_00043"}))) == 0     # 타-고객 승인자엔 안 보임
    assert len(q.list_pending(frozenset({"cust_00042"}))) == 1
    assert len(q.list_pending(None)) == 1                          # None = 전체(감사)


# ── P0-5: 공백/오타 APP_ENV 는 fail-closed ──

class _W:
    customers = [{"id": "c", "email": "e@x.com", "account": "1", "phone": "p", "rrn": "r"}]


def test_p0_5_malformed_appenv_failclosed():
    os.environ.pop("SAFEAGENT_API_KEYS", None)
    for env in (" PROD ", "Prod", "staging", "production"):
        with pytest.raises(RuntimeError):
            build_registry(_W(), app_env=env)          # 주입키 없음 → 데모키 거부
    assert set(build_registry(_W(), app_env="dev")).issuperset({"demo-agent-key"})


# ── imp-1: in_flight 항목은 축출되지 않음 ──

def test_imp1_idem_store_never_evicts_in_flight():
    s = server._IdemStore(maxsize=1, ttl_s=1000)
    s.set(("p", "k1"), {"hash": "h", "status": "in_flight", "response": None})
    s.set(("p", "k2"), {"hash": "h", "status": "done", "response": 2})   # maxsize=1 초과
    assert s.get(("p", "k1")) is not None      # in_flight 는 보존(축출 금지)


# ── imp-3: 잘못된 env 설정은 시작 시점 fail-loud ──

def test_imp3_invalid_env_config_raises():
    with pytest.raises(RuntimeError):
        server._pos_int("X", "0")
    with pytest.raises(RuntimeError):
        server._pos_float("Y", "-1")
    with pytest.raises(RuntimeError):
        server._pos_float("Z", "inf")


# ── P0-1: 서로 다른 principal 이 같은 Idempotency-Key 를 써도 백엔드 충돌 없음 ──

# ── R3-1: 멱등성 키 단사 인코딩(':' 접두 충돌 없음) ──

def test_r31_idem_key_injective():
    from src.agent.loop import idem_ns
    # ('agent','demo:foo') vs ('agent:demo','foo') — ':' 결합이면 동일 충돌, 단사면 상이
    assert idem_ns("agent", "demo:foo", 1) != idem_ns("agent:demo", "foo", 1)
    assert idem_ns("a", "b", 1) == idem_ns("a", "b", 1)          # 결정적
    assert idem_ns("a", "b", 1) != idem_ns("a", "b", 2)          # ordinal 반영


# ── R3-2: 빈 customer_ids 는 쓰기 불가(fail-closed) ──

def test_r32_empty_customer_scope_rejected():
    keys = {"attacker": {"id": "agent:atk", "role": "agent", "customer_ids": [],
                         "verified_accounts": ["110-000-000001"]}}
    os.environ["SAFEAGENT_API_KEYS"] = json.dumps(keys)
    try:
        with TestClient(server.app) as client:
            server.set_provider(FakeProvider.scripted(
                [[("issue_refund", {"account": "110-000-000001", "amount": 100, "reason": "x"})], "ok"]))
            # customer_id 없음 → 400(빈 scope fail-closed)
            r1 = client.post("/v1/agent/run", headers={"X-API-Key": "attacker"}, json={"input": "환불"})
            assert r1.status_code == 400
            # 담당하지 않는 고객 지정 → 403
            r2 = client.post("/v1/agent/run", headers={"X-API-Key": "attacker"},
                             json={"input": "환불", "customer_id": "cust_00042"})
            assert r2.status_code == 403
    finally:
        os.environ.pop("SAFEAGENT_API_KEYS", None)


# ── R3-3: /v1/audit 가 테넌트 scope 로 필터 ──

def test_r33_audit_tenant_scoped():
    keys = {
        "keyA": {"id": "agent:A", "role": "agent", "customer_ids": ["cust_00042"]},
        "auditB": {"id": "auditor:B", "role": "auditor", "customer_ids": ["cust_00043"]},
        "auditA": {"id": "auditor:A", "role": "auditor", "customer_ids": ["cust_00042"]},
    }
    os.environ["SAFEAGENT_API_KEYS"] = json.dumps(keys)
    try:
        with TestClient(server.app) as client:
            server.set_provider(FakeProvider.scripted(
                [[("send_email", {"to": "minjun.kim@customer.example.com",
                                  "subject": "[cust42] 기밀", "body": "b"})], "ok"]))
            client.post("/v1/agent/run", headers={"X-API-Key": "keyA"},
                        json={"input": "메일", "customer_id": "cust_00042"})
            # cust43 auditor 는 cust42 레코드를 보면 안 됨
            aB = client.get("/v1/audit", headers={"X-API-Key": "auditB"}).json()
            assert aB["email_count"] == 0, aB
            # cust42 auditor 는 봄
            aA = client.get("/v1/audit", headers={"X-API-Key": "auditA"}).json()
            assert aA["email_count"] == 1
    finally:
        os.environ.pop("SAFEAGENT_API_KEYS", None)


# ── R3-5: in_flight 가 저장소 상한을 넘으면 새 요청은 503 ──

def test_r35_inflight_capacity_saturation():
    from starlette.testclient import TestClient as TC
    # maxsize=1 로 축소한 store 주입 + in_flight 1건 선점 → 다음 키 요청은 503
    with TC(server.app) as client:
        server.set_provider(FakeProvider.scripted([[("send_email",
            {"to": "minjun.kim@customer.example.com", "subject": "s", "body": "b"})], "ok"]))
        server._state["idem"] = server._IdemStore(maxsize=1)
        server._state["idem"].set(("agent:demo", "occupied"), {"hash": "h", "status": "in_flight", "response": None})
        r = client.post("/v1/agent/run", headers={"X-API-Key": "demo-agent-key", "Idempotency-Key": "new"},
                        json={"input": "메일", "customer_id": "cust_00042"})
        assert r.status_code == 503 and "saturated" in str(r.json())


# ── R3-7: '전부 차단' 문구는 실제 차단결과일 때만 ──

def test_r37_bonferroni_wording_gated():
    from eval.expansion_v2 import _analyze
    from src.config import ROOT
    rows = []
    for scn in ("s1", "s2"):
        rows.append({"arm": "none", "kind": "attack", "scenario": scn, "attack_style": "a",
                     "executed": True, "finish_reason": "done"})
        rows.append({"arm": "plea", "kind": "attack", "scenario": scn, "attack_style": "a",
                     "executed": True, "finish_reason": "done"})   # plea 아무것도 안 막음
    out = ROOT / "results" / "_r37.md"
    txt = _analyze(rows, out)
    assert "plea 는 none 이 성공한" not in txt or "전부 차단한다" not in txt   # 거짓 '전부 차단' 없음
    assert "전부 차단 아님" in txt
    out.unlink()


def test_p0_1_cross_principal_no_backend_collision():
    keys = {
        "keyA": {"id": "agent:A", "role": "agent", "customer_ids": ["cust_00042"]},
        "keyB": {"id": "agent:B", "role": "agent", "customer_ids": ["cust_00043"]},
        "keyAudit": {"id": "auditor:x", "role": "auditor", "customer_ids": ["cust_00042", "cust_00043"]},
    }
    os.environ["SAFEAGENT_API_KEYS"] = json.dumps(keys)
    try:
        with TestClient(server.app) as client:      # dev + 주입키 → 두 테넌트 레지스트리
            hdr = {"Idempotency-Key": "SHARED-K"}
            server.set_provider(FakeProvider.scripted(
                [[("send_email", {"to": "minjun.kim@customer.example.com", "subject": "A", "body": "x"})], "ok"]))
            r1 = client.post("/v1/agent/run", headers={**hdr, "X-API-Key": "keyA"},
                             json={"input": "A", "customer_id": "cust_00042"})
            server.set_provider(FakeProvider.scripted(
                [[("send_email", {"to": "seoyeon.lee@customer.example.com", "subject": "B", "body": "y"})], "ok"]))
            r2 = client.post("/v1/agent/run", headers={**hdr, "X-API-Key": "keyB"},
                             json={"input": "B", "customer_id": "cust_00043"})
            assert r1.status_code == 200 and r2.status_code == 200
            # 같은 Idempotency-Key 라도 principal namespace 로 분리 → 둘 다 발송
            n = client.get("/v1/audit", headers={"X-API-Key": "keyAudit"}).json()["email_count"]
            assert n == 2, f"cross-principal 충돌: outbox={n}"
    finally:
        os.environ.pop("SAFEAGENT_API_KEYS", None)
