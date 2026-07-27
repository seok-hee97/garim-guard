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
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..config import ROOT, gen_config, load_policy
from . import log as alog
from . import tools as toolmod
from .authz import registry_from_world
from .backend import service_world
from .hitl import ApprovalQueue
from .loop import run_episode
from .policy import DefenseHooks

# 서버 고정 방어(클라이언트가 못 끔). ★데모/프로토타입 기본값★.
# tier_b 는 기본 비활성(value-provenance 오탐) — confused-deputy 는 authz(principal 바인딩)로 차단.
SERVER_DEFENSES: dict[str, Any] = {"tier_a": True, "dlp_vault": True}

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
    if os.getenv("APP_ENV") != "prod":                 # prod 는 오케스트레이터 env 주입만 신뢰
        from dotenv import load_dotenv
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

    # 상태 있는 백엔드(부작용·idempotency) + HITL 승인 큐 + principal 레지스트리(싱글턴).
    world = service_world()
    _state["world"] = world
    _state["hitl"] = ApprovalQueue()
    _state["principals"] = registry_from_world(world)
    _state["default_principal"] = _state["principals"]["demo-agent-key"]

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


class RunRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=8000)
    scenario: str = Field("", max_length=200)


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
    approver: str = Field("human", max_length=100)


def _business_status(ep) -> str:
    if ep.finish_reason == "hitl_pending":
        return "hitl_pending"
    if ep.finish_reason != "done":
        return ep.finish_reason
    if any(h.gate_decision in ("BLOCK", "HITL_DENY") for h in ep.hops):
        return "completed_with_blocks"
    return "completed"


def _resolve_principal(x_api_key: str | None):
    """X-API-Key → principal. ★fail-closed★ — 키 없거나 미등록이면 401(무인증 전-고객 접근 차단).
    데모 키: `demo-agent-key`. 실운영은 IdP/OAuth 로 교체."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key 헤더가 필요합니다")
    p = _state["principals"].get(x_api_key)
    if p is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키")
    return p


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    if not _state.get("ready"):
        raise HTTPException(status_code=503, detail="provider not ready")
    return {"status": "ready", "provider": _state["cfg"].provider, "rail": _state.get("rail_name")}


@app.post("/v1/agent/run", response_model=RunResponse)
async def run(req: RunRequest, x_api_key: str | None = Header(default=None)) -> RunResponse:
    if not _state.get("ready") or _state.get("provider") is None:
        raise HTTPException(status_code=503, detail="provider not ready")

    episode_id = uuid.uuid4().hex[:12]
    alog.bind(episode_id=episode_id)
    principal = _resolve_principal(x_api_key)
    hooks = DefenseHooks(_state["policy"], dict(SERVER_DEFENSES),
                         rail=_state["rail"], principal=principal)
    ep = await run_episode(
        req.input, _state["provider"], _state["cfg"],
        hooks=hooks, world=_state["world"], defenses=dict(SERVER_DEFENSES),
        scenario=req.scenario, episode_id=episode_id, hitl_queue=_state["hitl"],
    )
    return RunResponse(
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


@app.get("/v1/approvals")
async def list_approvals() -> dict:
    """대기 중인 HITL 승인 목록(원본 PII 는 마스킹)."""
    return {"pending": [a.public() for a in _state["hitl"].list_pending()]}


@app.post("/v1/approvals/{approval_id}")
async def resolve_approval(approval_id: str, req: ApproveRequest) -> dict:
    """승인/거부. 승인 시 보류된 액션을 백엔드에 실제 실행(idempotent, approval_id 를 키로)."""
    q = _state["hitl"]
    appr = q.get(approval_id)
    if appr is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if appr.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {appr.status}")

    world = _state["world"]

    def _executor(tool: str, args: dict) -> dict:
        # 승인된 액션을 백엔드에 실행. approval_id 를 idempotency_key 로 → 중복 승인 시 이중환불 방지.
        raw = toolmod.execute(tool, dict(args), approval_id, world=world)
        return {"executed": True, "result": raw}

    resolved = q.resolve(approval_id, approve=(req.decision == "approve"),
                         approver=req.approver,
                         executor=_executor if req.decision == "approve" else None)
    # executor 실패 시 approved 로 커밋되지 않음(pending 유지) → 재시도 가능한 502(자금 손실 방지).
    if req.decision == "approve" and resolved.status != "approved":
        alog.event(alog.get_logger(), "error", "approval_execution_failed",
                   id=approval_id, err=(resolved.result or {}).get("error"))
        raise HTTPException(status_code=502,
                            detail={"error": "backend_execution_failed",
                                    "retryable": True, "approval": resolved.public()})
    alog.event(alog.get_logger(), "info", "approval_resolved",
               id=approval_id, decision=req.decision, approver=req.approver)
    return resolved.public()


@app.get("/v1/audit")
async def audit() -> dict:
    """관측 — 처리된 환불·발송 이메일 요약(운영자 감사용)."""
    return _state["world"].audit()


def set_provider(provider: Any) -> None:
    """테스트/오프라인 주입용 — 실 API 없이 서버 엔드포인트를 구동한다."""
    _state["provider"] = provider
    _state["ready"] = True
