"""SafeAgent-KR HTTP 서비스 — run_episode 를 감싼 실제 구동 런타임.

앱 기동 시 policy/provider/rail 을 1회 구성(싱글턴), 요청마다 vault(요청 스코프)를 새로 만든다.
`POST /v1/agent/run` 이 4-seam 에이전트 루프를 실행하고 트레이스 요약을 반환한다.

★방어(defenses)는 서버 구성 고정 — 클라이언트가 약화시킬 수 없다★ (tier_a 항상 on,
dlp_vault 항상 on, strict_schema on). 클라이언트가 tier_a 를 끄는 경로가 존재하지 않는다.

    uvicorn src.agent.server:app --port 8000
    curl -s localhost:8000/healthz
    curl -s -XPOST localhost:8000/v1/agent/run -H 'content-type: application/json' \
         -d '{"input":"환불 처리 방법 알려주세요"}'
"""

from __future__ import annotations

import copy
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import ROOT, gen_config, load_policy
from . import log as alog
from .loop import run_episode
from .policy import DefenseHooks
from .toolworld import default_world

# 서버 고정 방어(클라이언트가 못 끔). 프로덕션 안전 기본값.
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
    _state["policy"] = policy
    _state["cfg"] = gen_config(policy)
    _state["rail"] = _build_rail()
    try:                                               # provider 구성 실패해도 부팅은 성공(readyz=false)
        _state["provider"] = _build_provider(policy)
        _state["ready"] = True
    except Exception as e:  # noqa: BLE001
        _state["provider"] = None
        _state["ready"] = False
        alog.event(logger, "error", "provider_init_failed", err=type(e).__name__)
    alog.event(logger, "info", "startup", provider=_state["cfg"].provider,
               model=_state["cfg"].model, ready=_state["ready"])
    yield
    _state.clear()


app = FastAPI(title="SafeAgent-KR", version="1.0", lifespan=lifespan)


class RunRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=8000)
    scenario: str = ""


class HopView(BaseModel):
    tool: str
    executed: bool
    decision: str
    rule: str


class RunResponse(BaseModel):
    episode_id: str
    finish_reason: str
    final_text: str
    hops: list[HopView]
    blocked_rules: list[str]
    latency_ms: int


@app.get("/healthz")
async def healthz() -> dict:
    """살아있음(liveness) — 의존성 무관, 항상 200."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    """준비됨(readiness) — provider 까지 구성돼 요청 처리 가능한가."""
    if not _state.get("ready"):
        raise HTTPException(status_code=503, detail="provider not ready")
    return {"status": "ready", "provider": _state["cfg"].provider}


@app.post("/v1/agent/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    if not _state.get("ready") or _state.get("provider") is None:
        raise HTTPException(status_code=503, detail="provider not ready")

    episode_id = uuid.uuid4().hex[:12]
    alog.bind(episode_id=episode_id)
    # 요청 스코프: DefenseHooks(→ 새 vault) 를 매 요청 새로 만든다.
    hooks = DefenseHooks(_state["policy"], dict(SERVER_DEFENSES), rail=_state["rail"])
    ep = await run_episode(
        req.input, _state["provider"], _state["cfg"],
        hooks=hooks, world=default_world(), defenses=dict(SERVER_DEFENSES),
        scenario=req.scenario, episode_id=episode_id,
    )
    return RunResponse(
        episode_id=ep.episode_id,
        finish_reason=ep.finish_reason,
        final_text=ep.final_text,
        hops=[HopView(tool=h.tool_name, executed=h.executed,
                      decision=h.gate_decision, rule=h.gate_rule)
              for h in ep.hops if h.tool_name],
        blocked_rules=sorted({h.gate_rule for h in ep.hops
                              if h.gate_decision == "BLOCK" and h.gate_rule}),
        latency_ms=ep.total_latency_ms,
    )


def set_provider(provider: Any) -> None:
    """테스트/오프라인 주입용 — 실 API 없이 서버 엔드포인트를 구동한다."""
    _state["provider"] = provider
    _state["ready"] = True
