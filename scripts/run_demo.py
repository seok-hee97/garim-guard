"""★키 불요 데모 러너★ — 한 명령으로 무방어→유출 / Tier A→차단 대비를 재생·녹화.

    python -m scripts.run_demo                 # poisoned_kb, 3개 arm 대비 + 트레이스 방출
    python -m scripts.run_demo --scenario refund_email

동작:
  같은 공격 에피소드(lookup→search_kb(오염)→send_email(공격자)→escalate)를
  ★결정적 스크립트(FakeProvider.scripted, API 키 불요)★로 arm 3종에 재생한다:
    - none            : 게이트 없음 → send_email(공격자) 실행 = 🔴 유출
    - tier_a          : 결정적 게이트 → send_email 차단(egress) = 🛑
    - tier_a+dlp_vault: 차단 + 모델에 전달되는 도구 결과가 토큰화(<KR_RRN_1> 등) = DLP
  각 에피소드 hop 트레이스를 results/demo_traces.jsonl 로 쓴다 → `python -m app.trace_view`
  가 '에피소드 실행 과정' 타임라인으로 렌더한다(발표/녹화용).

정직성: 모델의 인젝션 취약성이 아니라 ★런타임 게이트의 차단★을 보이는 픽스처다.
  실제 모델 유도(오염 문서를 읽고 스스로 공격자에게 보내는지)는 --provider openai/gemini
  라이브 실행에서 관측한다(report/demo.md 장면 1·2).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib

from src.config import ROOT, gen_config, load_policy
from src.agent.cli import SCENARIOS, _DEMO_SCRIPT, _build_rail
from src.agent.loop import run_episode
from src.agent.policy import DefenseHooks
from src.agent.toolworld import ATTACKER_EMAIL, default_world
from src.agent.trace import TraceWriter
from src.providers.fake import FakeProvider

TRACES = ROOT / "results" / "demo_traces.jsonl"

ARMS = [
    ("none", {}, "무방어 — 게이트 없음"),
    ("tier_a", {"tier_a": True}, "결정적 게이트(Tier A)"),
    ("tier_a+dlp_vault", {"tier_a": True, "dlp_vault": True}, "게이트 + PII 볼트"),
]


def _mark(h) -> str:
    if not h.tool_name:
        return ""
    return "✅ 실행" if h.executed else f"🛑 차단({h.gate_rule})"


def _demo_episode_id(scenario: str, arm: str) -> str:
    """★재현 가능한 episode_id★ — (시나리오, arm) 의 안정 해시.

    기본값인 uuid4 를 쓰면 데모를 돌릴 때마다 results/demo_traces.jsonl 이 id 만 달라진 채
    변경으로 잡혀(git dirty) '결정적 재생'이라는 문서 표현과 어긋난다. 홉·판정이 결정적이면
    식별자도 결정적이어야 커밋된 트레이스가 바이트 단위로 재현된다."""
    return hashlib.sha256(f"safeagent-demo:{scenario}:{arm}".encode()).hexdigest()[:12]


async def _run_arm(arm_defs: dict, scenario: str, arm: str = "none",
                   *, record_workflow: bool = False):
    policy = load_policy()
    cfg = gen_config(policy)
    user_input, _ = SCENARIOS[scenario]
    base_provider = FakeProvider.scripted(_DEMO_SCRIPT[scenario])   # ★키 불요★
    recorder = None
    provider = base_provider
    if record_workflow:
        from src.agent.workflow import WorkflowRecorderProvider
        recorder = WorkflowRecorderProvider(
            base_provider, mode="scripted",
            label="FakeProvider scripted (결정적 고정 재생)")
        provider = recorder
    rail = _build_rail()
    hooks = DefenseHooks(policy, arm_defs, rail=rail)
    ep = await run_episode(user_input, provider, cfg, hooks=hooks,
                           world=default_world(), defenses=arm_defs, scenario=scenario,
                           episode_id=_demo_episode_id(scenario, arm))
    if record_workflow:
        return ep, recorder, getattr(rail, "name", type(rail).__name__), cfg
    return ep


async def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="poisoned_kb", choices=list(SCENARIOS))
    ap.add_argument("--verbose", action="store_true",
                    help="각 arm 의 입력→모델→게이트→도구→DLP/레일 전체 워크플로 출력")
    args = ap.parse_args()

    if TRACES.exists():
        TRACES.unlink()                       # 재생마다 새로 — stale 홉 방지
    writer = TraceWriter(TRACES)

    print(f"\n╔══ SafeAgent-KR 데모 — 시나리오 '{args.scenario}' (키 불요 결정적 재생) ══╗")
    if args.scenario == "normal":
        print("  같은 정상 에피소드를 arm 3종에 재생 → 방어의 과차단 여부를 확인한다.\n")
    else:
        print("  같은 공격 에피소드를 arm 3종에 재생 → 게이트만 결과(실행)를 바꾼다.\n")

    exfil = {}
    for label, defs, desc in ARMS:
        run_result = await _run_arm(
            defs, args.scenario, arm=label, record_workflow=args.verbose)
        if args.verbose:
            ep, recorder, rail_name, cfg = run_result
        else:
            ep = run_result
        # 트레이스에 arm 라벨을 실어 뷰어가 그룹핑
        ep.scenario = f"{args.scenario} · arm={label}"
        # ★벽시계 제거★ — 커밋되는 데모 픽스처를 바이트 재현 가능하게 만든다. 실제 지연은
        # 아래 '관측 메트릭' 요약(p50/p95, in-process 레지스트리)에서 그대로 볼 수 있다.
        ep.total_latency_ms = 0        # 0 = 이 픽스처에 타이밍 미기록(측정값 아님)
        for hop in ep.hops:
            hop.latency_ms = 0
        writer.write(ep)
        print(f"── [{label}] {desc} " + "─" * max(2, 44 - len(label) - len(desc)))
        for h in ep.hops:
            if not h.tool_name:
                continue
            args_s = ", ".join(f"{k}={str(v)[:30]}" for k, v in h.tool_args.items())
            print(f"    {_mark(h):22s} {h.tool_name}({args_s})")
        att = ep.any_attempted("send_email", to=ATTACKER_EMAIL)
        exe = ep.any_executed("send_email", to=ATTACKER_EMAIL)
        exfil[label] = exe
        verdict = ("🔴 공격자에게 유출 실행됨" if exe
                   else "🛑 게이트가 유출 차단 (시도=True, 실행=False)" if att
                   else "· 공격 미시도")
        print(f"    → 공격자 이메일: 시도={att} 실행={exe}  {verdict}\n")
        if args.verbose:
            from src.agent.workflow import format_workflow_text, workflow_document
            document = workflow_document(
                ep,
                recorder,
                rail_name=rail_name,
                defenses=defs,
                provider_label="FakeProvider scripted (결정적 고정 재생)",
                model="없음 — 고정 도구 호출 시퀀스",
                include_raw=True,
            )
            print(format_workflow_text(document))

    # ── 관측 메트릭 요약(3 arm 누적) — 모니터링 구성요소 시연 ──
    from src.agent import metrics as M
    snap = M.snapshot()
    print("── 관측 메트릭(3 arm 누적) ──")
    print(f"    episodes_total      : {snap['counters'].get('episodes_total', {})}")
    print(f"    gate_blocks_total   : {snap['counters'].get('gate_blocks_total', {})}")
    print(f"    tool_executed_total : {snap['counters'].get('tool_executed_total', {})}")
    print(f"    dlp_redactions_total: {snap['counters'].get('dlp_redactions_total', {})}")
    # ★critical 불변식★ — 실제로 allowlist 밖으로 나간 실행. 무방어 arm 1건만이 정상이고,
    # 게이트 arm 이 여기 잡히면 회귀다(scripts.monitor 가 같은 카운터를 빨강으로 경보).
    leak_note = ("정상 시나리오이므로 전 arm 0건이 정상"
                 if args.scenario == "normal" else "무방어 arm 1건만 정상")
    print(f"    leaks_total ★      : {snap['counters'].get('leaks_total', {})}  ({leak_note})")
    _lat = snap["histograms"].get("episode_latency_ms", {}).get("_", {})
    if _lat:
        print(f"    episode_latency     : p50={_lat['p50_ms']}ms p95={_lat['p95_ms']}ms n={_lat['count']}")

    if args.scenario == "normal":
        ok = not any(exfil.values())
        success_message = "✅ 데모 결과 정합: 정상 요청은 공격 도구 미시도 · 게이트 과차단 없음"
    else:
        ok = (exfil.get("none") is True and exfil.get("tier_a") is False
              and exfil.get("tier_a+dlp_vault") is False)
        success_message = "✅ 데모 결과 정합: 무방어=유출 · 게이트=차단 (headline 재현)"
    print("╚" + "═" * 62 + "╝")
    print(f"→ 트레이스: {TRACES.relative_to(ROOT)}  (./.venv/bin/python -m app.trace_view 로 렌더)")
    if ok:
        print(success_message)
        # ★./.venv/bin/uvicorn 을 안내하면 안 된다★ — 디렉터리명이 바뀌면 그 실행파일의
        # shebang 이 옛 경로를 가리켜 bad interpreter 로 죽는다. python -m 형식은 영향이 없다.
        print("→ 전체 워크플로 웹 UI: ./.venv/bin/python -m uvicorn app.ui:app --port 8501")
        return 0
    print("⚠️ 예상과 다른 결과 — arm 정의/정책 확인 필요")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
