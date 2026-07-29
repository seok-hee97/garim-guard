"""브라우저 UI·CLI가 공유하는 전체 워크플로 데모 실행기."""

from __future__ import annotations

import copy
from typing import Any

from dotenv import load_dotenv

from ..config import ROOT, gen_config, load_policy
from .backend import service_world
from .cli import SCENARIOS, _build_provider, _build_rail
from .hitl import ApprovalQueue
from .loop import run_episode
from .policy import DefenseHooks
from .workflow import WorkflowRecorderProvider, workflow_document

DEFENSE_PRESETS: dict[str, dict[str, bool]] = {
    "none": {},
    "tier_a": {"tier_a": True},
    "tier_a+dlp_vault": {"tier_a": True, "dlp_vault": True},
    "tier_a+tier_b+dlp_vault": {
        "tier_a": True,
        "tier_b": True,
        "dlp_vault": True,
    },
}


async def run_workflow_demo(
    *,
    scenario: str = "poisoned_kb",
    provider_mode: str = "scripted",
    defense_preset: str = "tier_a+dlp_vault",
    user_input: str = "",
) -> dict[str, Any]:
    """한 에피소드를 실행하고 UI 친화적인 전체 워크플로 문서를 반환한다.

    scripted 는 합성 ToolWorld + 고정 호출만 사용하므로 raw 도구 결과를 로컬 화면에
    보여준다. live provider 는 실제 입력일 수 있어 raw 결과를 응답 문서에서 제외한다.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"알 수 없는 시나리오: {scenario}")
    if provider_mode not in ("scripted", "gemini", "openai"):
        raise ValueError(f"알 수 없는 provider: {provider_mode}")
    if defense_preset not in DEFENSE_PRESETS:
        raise ValueError(f"알 수 없는 방어 프리셋: {defense_preset}")

    load_dotenv(ROOT / ".env")
    policy = copy.deepcopy(load_policy())
    # 실제 프로그램 경로와 동일한 한국어 시스템 지시를 데모에도 적용한다.
    policy.setdefault("agent", {})["system_instruction_file"] = \
        "config/system_instruction.ko.txt"
    if provider_mode != "scripted":
        policy["provider"]["active"] = provider_mode
    cfg = gen_config(policy)

    scenario_input, description = SCENARIOS[scenario]
    effective_input = (user_input or "").strip() or scenario_input
    base_provider = (
        _build_provider("scripted", scenario)
        if provider_mode == "scripted"
        else _build_provider(provider_mode, scenario)
    )
    provider_label = (
        "FakeProvider scripted (결정적 고정 재생)"
        if provider_mode == "scripted"
        else f"{provider_mode} live"
    )
    recorder = WorkflowRecorderProvider(
        base_provider, mode=provider_mode, label=provider_label
    )
    rail = _build_rail()
    rail_name = getattr(rail, "name", type(rail).__name__)
    defenses = dict(DEFENSE_PRESETS[defense_preset])
    hooks = DefenseHooks(policy, defenses, rail=rail)
    world = service_world()
    hitl = ApprovalQueue()

    episode = await run_episode(
        effective_input,
        recorder,
        cfg,
        hooks=hooks,
        world=world,
        defenses=defenses,
        scenario=scenario,
        hitl_queue=hitl,
    )
    audit = world.audit(customer_ids=None)
    document = workflow_document(
        episode,
        recorder,
        rail_name=rail_name,
        defenses=defenses,
        provider_label=provider_label,
        model=("없음 — 고정 도구 호출 시퀀스" if provider_mode == "scripted" else cfg.model),
        include_raw=(provider_mode == "scripted"),
        audit=audit,
    )
    document["meta"]["scenario_description"] = description
    document["meta"]["defense_preset"] = defense_preset
    document["meta"]["rail_fallback"] = rail_name == "heuristic"
    document["hitl"] = {
        "pending": [a.public() for a in hitl.list_pending(customer_ids=None)]
    }
    return document
