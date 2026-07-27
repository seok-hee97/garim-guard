"""애블레이션 러너 — 케이스 × defenses arm 스윕. 증분·재개 가능.

    python -m eval.run --split test --provider openai              # 전체 arm 스윕
    python -m eval.run --split test --provider openai --limit 6    # 스모크(케이스 6개)
    python -m eval.run --split test --arms none,tier_a             # 특정 arm 만

각 (case, arm) 에피소드 채점 결과를 results/runs.jsonl 에 append. 이미 있는 (case,arm)은
건너뛴다(재개). 이후 eval/ablation.py 가 이 파일을 집계해 표를 만든다.

⚠️ provider=openai 권장 — 무료 Gemini 5 RPM 병목 회피(사용자 결정). gpt-4o-mini 저비용.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.config import ROOT, load_policy
from src.agent.trace import EpisodeTrace
from eval.build_dataset import CUSTOMERS
from eval.harness import run_case, score_attack, score_utility

DATA = ROOT / "data" / "kotip" / "v0.1"
RUNS = ROOT / "results" / "runs.jsonl"
TRACES = ROOT / "results" / "traces.jsonl"   # hop 단위 전체 트레이스(gate_rule·taint·rail·latency)

# 애블레이션 축 (defenses 조합). 각 arm = 표의 한 행.
ARMS: dict[str, dict] = {
    "none": {},
    "plea": {"plea": True},                                    # theater 기준선
    "spotlight": {"spotlight": True},                          # theater (데이터마킹)
    "classifier_gate": {"classifier_gate": {"enable": True, "threshold": 0.5}},  # theater (분류기 강제)
    "tier_a": {"tier_a": True},                                # genuine
    "tier_a_b": {"tier_a": True, "tier_b": True},
    "cg_plus_tier_a": {"tier_a": True, "classifier_gate": {"enable": True, "threshold": 0.5}},
}


def _load(split: str, kind: str) -> list[dict]:
    p = DATA / f"{kind}.{split}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _done() -> set[tuple[str, str]]:
    if not RUNS.exists():
        return set()
    out = set()
    for l in RUNS.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            out.add((r["arm"], r["case_id"]))
    return out


def _build_provider(name: str):
    if name == "openai":
        from src.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    from src.providers.gemini import GeminiProvider
    return GeminiProvider()


def _build_rail(name: str = "ko_agentguard"):
    """레일 선택. CPU: ko_agentguard/heuristic. GPU(RunPod): kanana/shieldgemma/e5."""
    if name == "kanana":
        from src.rails.kanana import KananaRail
        return KananaRail()
    if name == "shieldgemma":
        from src.rails.shieldgemma import ShieldGemmaRail
        return ShieldGemmaRail()
    if name == "e5":
        from src.rails.e5_lr import E5LRRail
        return E5LRRail()
    if name == "heuristic":
        from src.rails.base import HeuristicRail
        return HeuristicRail()
    try:
        from src.rails.ko_agentguard import KoAgentGuardRail
        return KoAgentGuardRail()
    except Exception:  # noqa: BLE001
        from src.rails.base import HeuristicRail
        return HeuristicRail()


async def _safe_run_case(case, provider, policy, defenses, *, rail, customers, arm) -> EpisodeTrace:
    """개별 케이스 실행을 격리한다. provider 오류(429/500)·레일 예외로 스윕 전체가
    죽지 않게 하고, 실패는 조용히 사라지는 대신 finish_reason='error' 에피소드로 남긴다."""
    try:
        return await run_case(case, provider, policy, defenses, rail=rail, customers=customers)
    except Exception as e:  # noqa: BLE001 — 케이스 실패 격리(관측 가능한 error 상태로 기록)
        print(f"  ⚠️ {arm}/{case['case_id']} ERROR: {type(e).__name__}: {e}")
        return EpisodeTrace(
            episode_id=case["case_id"], user_input=case.get("user_input", ""),
            scenario=case.get("scenario", ""), defenses=dict(defenses),
            finish_reason="error",
        )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--rail", default="ko_agentguard",
                    help="ko_agentguard|heuristic|kanana|shieldgemma|e5 (GPU 레일은 RunPod)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    policy = load_policy()
    policy["provider"]["active"] = args.provider   # gen_config 이 올바른 모델 ID 를 쓰도록
    # ★ 재현성: temp=0 으로 고정. 대응표본 애블레이션은 arm 간 모델 행동이 동일해야 한다.
    #   (temp>0 이면 같은 케이스의 attempted 가 arm 마다 달라져 t-ASR 비교가 오염된다.)
    for pv in ("openai", "gemini"):
        if pv in policy["provider"]:
            policy["provider"][pv]["temperature"] = 0.0
    provider = _build_provider(args.provider)
    rail = _build_rail(args.rail)
    arms = [a for a in args.arms.split(",") if a in ARMS]

    attacks = _load(args.split, "attacks")
    utility = _load(args.split, "utility")
    if args.limit:
        attacks = attacks[: args.limit]
    done = _done()
    RUNS.parent.mkdir(parents=True, exist_ok=True)

    total = len(arms) * (len(attacks) + len(utility))
    n = 0
    with RUNS.open("a", encoding="utf-8") as f, TRACES.open("a", encoding="utf-8") as tf:
        for arm in arms:
            defenses = ARMS[arm]
            for case in attacks:
                n += 1
                if (arm, case["case_id"]) in done:
                    continue
                ep = await _safe_run_case(case, provider, policy, defenses,
                                          rail=rail, customers=CUSTOMERS, arm=arm)
                tf.write(json.dumps({"arm": arm, **ep.to_dict()}, ensure_ascii=False) + "\n"); tf.flush()
                row = {"arm": arm, "kind": "attack", **score_attack(case, ep)}
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
                mark = ("⚠️" if row["finish_reason"] == "error" else
                        "🛑" if (row["attempted"] and not row["executed"]) else
                        "🔴" if row["executed"] else "·")
                print(f"[{n}/{total}] {arm:16s} {case['case_id']:44s} {mark} att={row['attempted']} exe={row['executed']}")
            for case in utility:
                n += 1
                if (arm, case["case_id"]) in done:
                    continue
                ep = await _safe_run_case(case, provider, policy, defenses,
                                          rail=rail, customers=CUSTOMERS, arm=arm)
                tf.write(json.dumps({"arm": arm, **ep.to_dict()}, ensure_ascii=False) + "\n"); tf.flush()
                row = {"arm": arm, "kind": "utility", **score_utility(case, ep)}
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
                print(f"[{n}/{total}] {arm:16s} {case['case_id']:44s} util_done={row['completed']}")
    print(f"\n완료. → {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
