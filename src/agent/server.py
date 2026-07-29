"""SafeAgent-KR HTTP 서비스 — run_episode 를 감싼 실제 구동 런타임.

앱 기동 시 policy/provider/rail/world/승인큐/principal 을 1회 구성(싱글턴), 요청마다
vault(요청 스코프)를 새로 만든다. 상태 있는 ServiceWorld 로 환불·이메일에 실제 부작용과
idempotency 를 갖고, 비가역 도구는 HITL 승인 큐를 거쳐 사람 승인 후 실행된다.

★방어 고정(클라이언트가 못 끔): tier_a + dlp_vault + strict_schema.★
★authz(principal↔고객 바인딩)로 confused-deputy 를 구조적으로 차단.★

엔드포인트:
  GET  /healthz /readyz
  POST /v1/agent/run            에이전트 실행 (X-API-Key 로 principal 인증)
  GET  /v1/approvals            대기 중 HITL 승인 목록
  POST /v1/approvals/{id}       승인/거부 → 승인 시 백엔드에 실제 실행(idempotent)
  GET  /v1/audit                처리된 환불·발송 이메일(관측)
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import ROOT, gen_config, load_policy
from . import log as alog
from . import metrics as M
from . import tools as toolmod
from .authz import build_registry, Principal, _norm_env
from .backend import service_world
from .hitl import ApprovalQueue
from .loop import run_episode, idem_ns
from .policy import DefenseHooks

# 서버 고정 방어(클라이언트가 못 끔). ★데모/프로토타입 기본값★.
# tier_b 는 기본 비활성(value-provenance 오탐) — confused-deputy 는 authz(principal 바인딩)로 차단.
SERVER_DEFENSES: dict[str, Any] = {"tier_a": True, "dlp_vault": True}

def _pos_float(name: str, default: str) -> float:
    """양의 유한 float env — 잘못된 값(0·음수·NaN/inf)은 시작 시점에 fail-loud(imp-3)."""
    import math
    v = float(os.getenv(name, default))
    if not math.isfinite(v) or v <= 0:
        raise RuntimeError(f"{name}={v} 유효하지 않음(양의 유한값이어야 함)")
    return v


def _pos_int(name: str, default: str) -> int:
    v = int(os.getenv(name, default))
    if v < 1:
        raise RuntimeError(f"{name}={v} 유효하지 않음(1 이상이어야 함)")
    return v


# 승인 만료(초) — 오래된 HITL 승인이 정책 변경 후 실행되지 않도록. env 로 조정 가능.
APPROVAL_TTL_S: float = _pos_float("APPROVAL_TTL_S", "3600")
IDEM_MAXSIZE: int = _pos_int("IDEM_MAXSIZE", "10000")
IDEM_TTL_S: float = _pos_float("IDEM_TTL_S", "86400")     # 요청 멱등성 창(기본 24h)


class _IdemStore:
    """요청 멱등성 저장소 — LRU maxsize + TTL. 평범한 dict 는 키마다 RunResponse 를 영구 보관해
    메모리가 무한 성장(가용성/DoS)하므로, 크기 상한 LRU 축출 + 만료를 강제한다(리뷰)."""

    def __init__(self, maxsize: int = IDEM_MAXSIZE, ttl_s: float = IDEM_TTL_S):
        self._d: "OrderedDict[Any, dict]" = OrderedDict()
        self._maxsize = maxsize
        self._ttl_s = ttl_s

    def _purge_expired(self) -> None:
        """TTL 지난 항목 일괄 정리(R4-#2) — in_flight 든 done 이든 만료분을 제거해,
        취소로 남은 stale in_flight 마커가 capacity 를 영구 점유(영구 503)하지 않게 한다."""
        now = time.time()
        dead = [k for k, e in self._d.items() if now - e.get("created_at", 0) > self._ttl_s]
        for k in dead:
            self._d.pop(k, None)

    def get(self, key: Any) -> dict | None:
        e = self._d.get(key)
        if e is None:
            return None
        if time.time() - e.get("created_at", 0) > self._ttl_s:
            self._d.pop(key, None)                 # 만료 → miss
            return None
        self._d.move_to_end(key)
        return e

    def set(self, key: Any, val: dict) -> None:
        v = dict(val); v["created_at"] = time.time()
        self._d[key] = v
        self._d.move_to_end(key)
        # ★in_flight 는 축출하지 않는다(imp-1)★ — 진행 중 원 요청의 마커가 사라지면 재시도가
        #   새 요청으로 접수돼 부작용이 이중 실행될 수 있다. 완료(done) 항목만 LRU 축출한다.
        while len(self._d) > self._maxsize:
            victim = next((k for k, e in self._d.items() if e.get("status") != "in_flight"), None)
            if victim is None:
                break                              # 전부 in_flight → 축출 보류(용량 초과 감수)
            self._d.pop(victim, None)

    def pop(self, key: Any, default: Any = None) -> Any:
        return self._d.pop(key, default)

    def inflight_count(self) -> int:
        """진행 중(in_flight) 항목 수 — active capacity 판정용. ★만료분 먼저 정리★(R4-#2)."""
        self._purge_expired()
        return sum(1 for e in self._d.values() if e.get("status") == "in_flight")

    def at_inflight_capacity(self) -> bool:
        """진행 중 요청이 저장소 상한만큼 쌓였는가 — 초과 시 새 요청 거부(R3-5). 만료 stale 제외."""
        return self.inflight_count() >= self._maxsize


_state: dict[str, Any] = {}


def _build_provider(policy: dict) -> Any:
    from .cli import _build_provider as bp
    return bp(policy["provider"]["active"])


def _build_rail() -> Any:
    from .cli import _build_rail as br
    return br()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = alog.configure(os.getenv("LOG_LEVEL", "INFO"))
    if _norm_env(os.getenv("APP_ENV")) in ("dev", "test", ""):   # build_registry 와 동일 규칙(P0-5)
        from dotenv import load_dotenv                            # prod 등은 오케스트레이터 env 주입만 신뢰
        load_dotenv(ROOT / ".env")

    policy = copy.deepcopy(load_policy())              # 캐시된 eval 정책과 격리
    policy.setdefault("tier_a", {})["strict_schema"] = True   # 서비스는 엄격 스키마
    _sys = ROOT / "config" / "system_instruction.ko.txt"
    if _sys.exists():
        policy.setdefault("agent", {})["system_instruction_file"] = "config/system_instruction.ko.txt"
    else:
        alog.event(logger, "warning", "system_instruction_missing", path=str(_sys))
    _state["policy"] = policy
    _state["cfg"] = gen_config(policy)
    # 요청 멱등성용 정책 지문(정책이 바뀌면 같은 키라도 다른 요청으로 취급).
    _state["policy_hash"] = hashlib.sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
    _state["idem"] = _IdemStore()    # (principal_id, Idempotency-Key) → {hash, status, response} (LRU+TTL)

    # 상태 있는 백엔드(부작용·idempotency) + HITL 승인 큐 + principal 레지스트리(싱글턴).
    world = service_world()
    _state["world"] = world
    _state["hitl"] = ApprovalQueue()
    # ★prod 는 데모 키 거부 + 주입 자격증명(SAFEAGENT_API_KEYS) 필수 → 없으면 부팅 실패(fail-closed).★
    _state["principals"] = build_registry(world, app_env=os.getenv("APP_ENV"))
    alog.event(logger, "info", "principals_loaded",
               n=len(_state["principals"]), env=os.getenv("APP_ENV", "dev"))

    rail = _build_rail()
    _state["rail"] = rail
    _state["rail_name"] = getattr(rail, "name", type(rail).__name__)
    if _state["rail_name"] == "heuristic":
        alog.event(logger, "warning", "rail_fallback_heuristic",
                   note="ko_agentguard 아티팩트 부재 → HeuristicRail 폴백")
    try:
        _state["provider"] = _build_provider(policy)
        _state["ready"] = True
    except Exception as e:  # noqa: BLE001 — provider 실패해도 부팅 성공(readyz=false)
        _state["provider"] = None
        _state["ready"] = False
        alog.event(logger, "error", "provider_init_failed", err=type(e).__name__)
    alog.event(logger, "info", "startup", provider=_state["cfg"].provider,
               model=_state["cfg"].model, rail=_state["rail_name"], ready=_state["ready"])
    yield
    _state.clear()


app = FastAPI(title="SafeAgent-KR", version="1.0", lifespan=lifespan)
_STARTED_AT = time.time()


@app.middleware("http")
async def _observe_requests(request: Request, call_next):
    """모든 HTTP 요청에 상관ID·타이밍·상태 구조적 로그 1줄 + 메트릭. ★PII 금지★ —
    라우트 템플릿(/v1/approvals/{approval_id})만 라벨로 쓰고 concrete path·query·body 미기록."""
    rid = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12])[:64]
    alog.bind(request_id=rid)
    t0 = time.perf_counter()
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        resp.headers["X-Request-ID"] = rid          # 클라이언트 상관용 에코
        return resp
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        route = request.scope.get("route")
        tmpl = getattr(route, "path", None) or "unmatched"   # 템플릿만(식별자 유출 방지)
        alog.event(alog.get_logger(), "info", "http_request",
                   method=request.method, route=tmpl, status=status,
                   latency_ms=latency_ms, request_id=rid)
        M.inc("http_requests_total", method=request.method, route=tmpl, status=status)
        M.observe("http_request_latency_ms", latency_ms, method=request.method, route=tmpl)


@app.get("/v1/metrics")
async def metrics_endpoint() -> dict:
    """라이브 관측 스냅샷 — 누적 카운터·게이지·지연 분위수 + in-flight + uptime.
    ★응답에 PII 없음★(레지스트리가 정수/enum 라벨만 보유). 스크레이퍼/monitor/사람이 읽는다."""
    snap = M.snapshot()
    cfg = _state.get("cfg")
    idem = _state.get("idem")
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "ready": bool(_state.get("ready")),
        "build": {"provider": getattr(cfg, "provider", ""), "model": getattr(cfg, "model", ""),
                  "rail": _state.get("rail_name", "")},
        "in_flight": {
            "episodes": snap["gauges"].get("episodes_in_flight", {}).get("_", 0),
            "idem": idem.inflight_count() if idem is not None else 0,
        },
        "counters": snap["counters"],
        "gauges": snap["gauges"],
        "histograms": snap["histograms"],
        "series_dropped": snap["series_dropped"],
    }


class RunRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=8000)
    scenario: str = Field("", max_length=200)
    customer_id: str = Field("", max_length=64)   # 대화 대상 고객 — 지정 시 스코프를 그 고객으로 한정


class HopView(BaseModel):
    tool: str
    executed: bool
    decision: str
    rule: str


class RunResponse(BaseModel):
    episode_id: str
    status: str                 # completed | completed_with_blocks | hitl_pending | blocked | error | ...
    finish_reason: str
    final_text: str
    hops: list[HopView]
    blocked_rules: list[str]
    pending_approval: str = ""  # hitl_pending 시 승인 요청 id
    latency_ms: int


class ApproveRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|deny)$")
    # approver 는 클라이언트가 지정하지 않는다 — 인증 토큰의 principal 로만 결정/기록(P1 로그위조 방지).


def _business_status(ep) -> str:
    if ep.finish_reason == "hitl_pending":
        return "hitl_pending"
    if ep.finish_reason != "done":
        return ep.finish_reason
    if any(h.gate_decision in ("BLOCK", "HITL_DENY") for h in ep.hops):
        return "completed_with_blocks"
    return "completed"


def _resolve_principal(x_api_key: str | None):
    """X-API-Key → principal. ★fail-closed★ — 키 없거나 미등록이면 401.
    데모 키: agent=`demo-agent-key`, 승인=`demo-approver-key`, 감사=`demo-auditor-key`."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key 헤더가 필요합니다")
    p = _state["principals"].get(x_api_key)
    if p is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키")
    return p


def _require(x_api_key: str | None, roles: set[str]):
    """인증 + 역할 인가(직무분리). 역할 불일치 시 403."""
    p = _resolve_principal(x_api_key)
    if p.role not in roles:
        raise HTTPException(status_code=403,
                            detail=f"역할 '{p.role}' 은 권한 없음(필요: {sorted(roles)})")
    return p


def _bind_customer(principal: Principal, customer_id: str, world: Any) -> Principal:
    """★대화-고객 바인딩(P1)★ — 요청이 지정한 고객으로 스코프를 '한 명'으로 좁힌다.

    principal 이 전체 고객을 flat 하게 소유하면 조회는 고객 A인데 발송/환불은 고객 B(등록됨)로
    가는 confused-deputy 가 남는다. customer_id 를 지정하면 그 고객의 이메일/계좌만 허용해
    타-고객 유출을 차단한다. principal 이 담당하지 않는 고객이면 403."""
    if customer_id not in principal.customer_ids:
        raise HTTPException(status_code=403,
                            detail=f"principal 이 고객 {customer_id} 를 담당하지 않습니다")
    cust = next((c for c in getattr(world, "customers", []) if c.get("id") == customer_id), None)
    if cust is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다")
    return Principal(
        id=principal.id, role=principal.role, customer_ids=frozenset({customer_id}),
        verified_emails=frozenset({cust["email"]} if cust.get("email") else set()),
        verified_accounts=frozenset({cust["account"]} if cust.get("account") else set()),
        verified_phones=frozenset({cust["phone"]} if cust.get("phone") else set()),   # 조회 읽기-스코프
        verified_rrns=frozenset({cust["rrn"]} if cust.get("rrn") else set()),
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    if not _state.get("ready"):
        raise HTTPException(status_code=503, detail="provider not ready")
    return {"status": "ready", "provider": _state["cfg"].provider, "rail": _state.get("rail_name")}


@app.post("/v1/agent/run", response_model=RunResponse)
async def run(req: RunRequest, x_api_key: str | None = Header(default=None),
              idempotency_key: str | None = Header(default=None)) -> RunResponse:
    if not _state.get("ready") or _state.get("provider") is None:
        raise HTTPException(status_code=503, detail="provider not ready")

    episode_id = uuid.uuid4().hex[:12]
    alog.bind(episode_id=episode_id)
    principal = _require(x_api_key, {"agent"})
    # ★default-secure 고객 바인딩(P1)★ — flat scope confused-deputy 를 기본 경로에서 막는다:
    #   customer_id 지정 → 그 고객으로 한정. 미지정이고 principal 이 단일 고객 → 암묵 바인딩.
    #   미지정인데 다중 고객 담당 → 400(대상 고객 불명확 시 쓰기 도구를 허용하지 않음).
    if req.customer_id:
        principal = _bind_customer(principal, req.customer_id, _state["world"])
    elif len(principal.customer_ids) == 1:
        principal = _bind_customer(principal, next(iter(principal.customer_ids)), _state["world"])
    else:
        # ★fail-closed(R3-2)★ — 다수 고객이면 대상 불명확, 빈 scope(0)면 담당 고객 없음 →
        #   어느 쪽이든 customer_id 없이는 쓰기 도구를 허용하지 않는다(빈 scope fall-through 차단).
        raise HTTPException(status_code=400, detail={
            "error": "customer_id_required",
            "reason": ("담당 고객이 없거나(빈 scope) 다수입니다 — 대상 고객을 customer_id 로 지정하세요")})

    # 요청 지문 — 멱등성 스코프(내용 인지)와 재생 검증에 공통 사용.
    req_hash = hashlib.sha256(json.dumps(
        {"input": req.input, "scenario": req.scenario, "customer_id": req.customer_id,
         "principal": principal.id, "policy": _state["policy_hash"],
         "provider": _state["cfg"].provider, "model": _state["cfg"].model},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    # ── ★요청 멱등성(P0-2)★ — (principal, Idempotency-Key)로 요청 저장. canonical hash 로
    #    같은 키/같은 요청은 원 응답 재생, 같은 키/다른 요청은 409, 진행중 재요청도 409. ──
    idem_store = _state["idem"]
    store_key = None
    if idempotency_key:
        store_key = (principal.id, idempotency_key)
        prev = idem_store.get(store_key)
        if prev is not None:
            if prev["hash"] != req_hash:
                raise HTTPException(status_code=409, detail={
                    "error": "idempotency_key_reused_with_different_request",
                    "idempotency_key": idempotency_key})
            if prev["status"] == "in_flight":
                raise HTTPException(status_code=409, detail={
                    "error": "request_in_progress", "idempotency_key": idempotency_key})
            return prev["response"]         # 완료된 원 응답 재생(부작용 재실행 없음)
        # ★in_flight 용량 상한(R3-5)★ — 진행 중 요청이 저장소 상한만큼 쌓이면 새 요청은 거부(503).
        if idem_store.at_inflight_capacity():
            raise HTTPException(status_code=503, detail={"error": "idempotency_store_saturated"})
        idem_store.set(store_key, {"hash": req_hash, "status": "in_flight", "response": None})

    hooks = DefenseHooks(_state["policy"], dict(SERVER_DEFENSES),
                         rail=_state["rail"], principal=principal)
    try:
        ep = await run_episode(
            req.input, _state["provider"], _state["cfg"],
            hooks=hooks, world=_state["world"], defenses=dict(SERVER_DEFENSES),
            scenario=req.scenario, episode_id=episode_id, hitl_queue=_state["hitl"],
            # ★단사·내용인지 멱등성 스코프(R3-1·R3-4)★ — (principal, 클라이언트키, 요청지문)을
            #   JSON 해시로 결합. principal 분리로 테넌트 충돌 없고, req_hash 포함으로 같은 키·다른
            #   요청이 백엔드에서 replay 로 오인되지 않는다(HTTP store 만료 후에도).
            idempotency_scope=idem_ns(principal.id, idempotency_key or episode_id, req_hash),
        )
    except BaseException:                     # ★취소(CancelledError)·오류 모두★ in_flight 마커 정리(R4-#2)
        if store_key is not None:
            idem_store.pop(store_key, None)   # 실패·취소는 굳히지 않음 → 재시도 허용
        raise
    resp = RunResponse(
        episode_id=ep.episode_id,
        status=_business_status(ep),
        finish_reason=ep.finish_reason,
        final_text=ep.final_text,
        hops=[HopView(tool=h.tool_name, executed=h.executed,
                      decision=h.gate_decision, rule=h.gate_rule)
              for h in ep.hops if h.tool_name],
        blocked_rules=sorted({h.gate_rule for h in ep.hops
                              if h.gate_decision == "BLOCK" and h.gate_rule}),
        pending_approval=ep.pending_approval,
        latency_ms=ep.total_latency_ms,
    )
    if store_key is not None:
        # ★일시적 실패(provider 429/timeout·deadline)는 굳히지 않는다★ — 예외 경로와 동일하게
        #   키를 비워 재시도가 재실행되게 한다(리뷰 #1). 종결 상태만 캐시.
        if ep.finish_reason in ("error", "deadline_exceeded"):
            idem_store.pop(store_key, None)
        else:
            idem_store.set(store_key, {"hash": req_hash, "status": "done", "response": resp})
    return resp


@app.get("/v1/approvals")
async def list_approvals(x_api_key: str | None = Header(default=None)) -> dict:
    """대기 중인 HITL 승인 목록(원본 PII 는 마스킹). 승인자/감사자만.
    ★테넌트 scope 강제(P0-2)★ — 담당 고객(principal.customer_ids)의 승인만 조회된다."""
    p = _require(x_api_key, {"approver", "auditor"})
    return {"pending": [a.public() for a in _state["hitl"].list_pending(p.customer_ids)]}


@app.post("/v1/approvals/{approval_id}")
async def resolve_approval(approval_id: str, req: ApproveRequest,
                           x_api_key: str | None = Header(default=None)) -> dict:
    """승인/거부. ★승인자 역할 필수 + 요청자≠승인자(직무분리)★. approver 는 클라이언트 문자열이
    아니라 인증 토큰에서 결정한다. 승인 시 보류 액션을 백엔드에 실행(idempotent)."""
    approver = _require(x_api_key, {"approver"})       # 승인자 역할만(에이전트 자기승인 차단)
    q = _state["hitl"]
    appr = q.get(approval_id)
    if appr is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if appr.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {appr.status}")
    if appr.principal and appr.principal == approver.id:   # 요청자≠승인자 강제
        raise HTTPException(status_code=403, detail="요청자와 승인자가 동일할 수 없습니다(직무분리)")

    # ── ★테넌트 scope 강제(P0-2·R3-2)★ — 담당하지 않는 고객의 승인은 조회·승인·거부 불가.
    #    ★빈 customer_id 승인은 '모두에게 승인 가능'이 아니라 '모두에게 거부'로 fail-closed★
    #    (scope 없는 비가역 승인이 아무 approver 에게나 실행되던 우회 차단).
    if not appr.customer_id or appr.customer_id not in approver.customer_ids:
        alog.event(alog.get_logger(), "warning", "approval_scope_denied",
                   id=approval_id, approver=approver.id, target_customer=(appr.customer_id or "<empty>"))
        raise HTTPException(status_code=403, detail={
            "error": "approval_out_of_tenant_scope",
            "reason": "승인자가 담당하지 않는(또는 scope 미지정) 고객의 승인입니다"})

    # ── ★승인 stale 방지(P1)★ — 오래된/정책 밖 승인이 실행되지 않게 재검사 ──
    if req.decision == "approve":
        import time as _time
        if appr.created_at and (_time.time() - appr.created_at) > APPROVAL_TTL_S:
            appr.status = "expired"
            raise HTTPException(status_code=409, detail={
                "error": "approval_expired", "ttl_s": APPROVAL_TTL_S})
        # 금액 한도 재검사(승인 시점의 최신 정책으로) — 오래된 승인이 하향된 한도를 우회 못 하게.
        if appr.tool == "issue_refund":
            spec = _state["policy"].get("tools", {}).get("issue_refund", {}).get("args", {})
            cap = (spec.get("amount", {}) or {}).get("max")
            amt = appr.args.get("amount")
            if cap is not None:
                try:                                     # 문자열 '1000000' 도 정규화(생성 게이트가 허용)
                    amt_num = int(amt)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=409, detail={   # 비수치 = fail-closed
                        "error": "refund_amount_not_numeric", "amount": amt})
                if amt_num > cap:
                    raise HTTPException(status_code=409, detail={
                        "error": "amount_exceeds_current_limit", "amount": amt_num, "current_max": cap})
                appr.args["amount"] = amt_num          # ★정규화값 저장(imp-2)★ — 백엔드에 int 전달

    world = _state["world"]

    # ★백엔드 멱등성 키 = 승인의 logical_key(P0-3)★ — approval_id 가 아니라 논리적 액션 키를 써야
    #   두 승인이 같은 논리적 환불이면 백엔드에서 한 번만 실행된다(방어 심화).
    backend_key = appr.logical_key or approval_id

    def _executor(tool: str, args: dict) -> dict:
        raw = toolmod.execute(tool, dict(args), approval_id, world=world,
                              idempotency_key=backend_key,   # 논리적 키로 백엔드 dedup
                              actor={"customer_id": appr.customer_id, "principal_id": appr.principal})
        return {"executed": True, "result": raw}

    resolved = q.resolve(approval_id, approve=(req.decision == "approve"),
                         approver=approver.id,          # ★토큰에서 결정(클라이언트 문자열 무시)★
                         executor=_executor if req.decision == "approve" else None)
    # executor 실패 시 approved 로 커밋되지 않음(pending 유지) → 재시도 가능한 502(자금 손실 방지).
    if req.decision == "approve" and resolved.status != "approved":
        alog.event(alog.get_logger(), "error", "approval_execution_failed",
                   id=approval_id, err=(resolved.result or {}).get("error"))
        raise HTTPException(status_code=502,
                            detail={"error": "backend_execution_failed",
                                    "retryable": True, "approval": resolved.public()})
    # ★감사 로그는 인증 토큰의 principal 만 기록(P1)★ — 클라이언트가 보낸 req.approver 문자열은
    #   실행 권한에도, 감사에도 쓰지 않는다(로그 위조 방지). req.approver 필드는 제거 예정.
    alog.event(alog.get_logger(), "info", "approval_resolved",
               id=approval_id, decision=req.decision, approver=approver.id)
    return resolved.public()


@app.get("/v1/audit")
async def audit(x_api_key: str | None = Header(default=None)) -> dict:
    """관측 — 처리된 환불·발송 이메일 요약(감사용). 감사자/승인자만 + PII 마스킹.
    ★테넌트 scope 강제(R3-3)★ — 담당 고객(principal.customer_ids)의 레코드만 반환한다."""
    p = _require(x_api_key, {"auditor", "approver"})
    return _state["world"].audit(customer_ids=p.customer_ids)


def set_provider(provider: Any) -> None:
    """테스트/오프라인 주입용 — 실 API 없이 서버 엔드포인트를 구동한다."""
    _state["provider"] = provider
    _state["ready"] = True
