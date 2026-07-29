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


def _provenance(policy: dict, split: str, rail: str = "ko_agentguard") -> dict:
    """실행 계보 — 코드/데이터/정책/SDK 가 바뀌면 결과가 다르다는 것을 각 행에 각인.

    run_id(조건 슬러그)만으로는 dataset/policy/commit 변경을 구분 못 해 stale 결과를 완료로
    오인할 수 있다(리뷰 지적). 아래를 함께 기록해 감사·재현 시 불일치를 즉시 드러낸다."""
    import hashlib
    import subprocess
    import json as _json

    def _sha(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()[:16]

    ds = b""
    for kind in ("attacks", "utility"):
        p = DATA / f"{kind}.{split}.jsonl"
        if p.exists():
            ds += p.read_bytes()
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — git 없거나 비-repo
        commit = "unknown"
    try:
        from importlib.metadata import version
        sdk = {p: version(p) for p in ("google-genai", "openai") if _safe_ver(p)}
    except Exception:  # noqa: BLE001
        sdk = {}
    # ★레일별 지문(R6-#3)★ — 선택한 레일에 맞는 아티팩트/모델만 계보에 넣는다.
    #   joblib 레일(ko_agentguard/e5)은 해당 파일 해시, HF 모델 레일(kanana/shieldgemma)은
    #   모델 id, heuristic 은 없음. (무관한 ko_agentguard joblib 을 항상 넣던 결함 수정.)
    _joblib = {"ko_agentguard": "ko-agentguard-lr.joblib", "e5": "e5-lr-head.joblib"}
    _model = {"kanana": "kakaocorp/kanana-safeguard-prompt-2.1b",
              "shieldgemma": "google/shieldgemma-2b"}
    rail_hash, rail_ref = "", ""
    if rail in _joblib:
        p = ROOT / "train" / "artifacts" / _joblib[rail]
        rail_hash = _sha(p.read_bytes()) if p.exists() else ""
    elif rail in _model:
        rail_ref = _model[rail]
    return {
        "dataset_hash": _sha(ds),
        "policy_hash": _sha(_json.dumps(policy, sort_keys=True, default=str).encode()),
        "rail_artifact_hash": rail_hash,   # joblib 레일 파일 해시(해당 레일만)
        "rail_model_ref": rail_ref,        # HF 모델 레일 id(kanana/shieldgemma)
        "commit": commit,
        "sdk": sdk,
    }


def _safe_ver(pkg: str) -> bool:
    try:
        from importlib.metadata import version
        version(pkg); return True
    except Exception:  # noqa: BLE001
        return False


def _warn_provenance_drift(prov: dict) -> None:
    """기존 runs.jsonl 행의 계보와 현재가 다르면 경고 — 코드/데이터 변경 후 stale 재사용 방지."""
    if not RUNS.exists():
        return
    seen_ds, seen_commit = set(), set()
    for l in RUNS.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r.get("dataset_hash"):
            seen_ds.add(r["dataset_hash"])
        if r.get("commit"):
            seen_commit.add(r["commit"])
    drift_ds = seen_ds - {prov["dataset_hash"]}
    if drift_ds:
        print(f"⚠️ dataset_hash 불일치: 기존 {sorted(seen_ds)} vs 현재 {prov['dataset_hash']} — "
              "데이터가 바뀌었으면 새 run_id 로 분리하거나 runs.jsonl 을 비우세요.")


def _run_id(provider: str, model: str, rail: str, split: str) -> str:
    """실험 조건 결정적 슬러그 — 같은 조건은 같은 run_id, 다른 조건은 격리.

    타임스탬프가 아니라 조건 함수라 재현 가능하고, 같은 파일에 여러 조건을 이어붙여도
    ablation 이 run_id 로 분리 집계할 수 있다. resume 키에는 넣지 않는다(넣으면 매 실행이
    새 run_id 라 절대 재개 못 함) — 조건 4-튜플이 이미 resume 격리를 담당한다."""
    return f"{split}.{provider}.{model}.{rail}"


def _load(split: str, kind: str) -> list[dict]:
    p = DATA / f"{kind}.{split}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _done() -> set[tuple]:
    """완료 키 = (arm, case_id, provider, model, rail). provider/model/rail 을 포함해
    조건을 바꿔 같은 파일에 이어붙여도 stale 결과를 재사용하지 않는다. finish_reason='error'
    (일시 실패)는 완료로 굳히지 않아 재개 시 재시도된다. 손상 라인은 건너뛴다."""
    if not RUNS.exists():
        return set()
    out = set()
    for l in RUNS.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r.get("finish_reason") == "error":
            continue
        # ★계보 포함(imp-4)★ — dataset/policy 지문을 완료 키에 넣어, 데이터·정책이 바뀌면
        #   같은 (arm,case,provider,model,rail,split) 이라도 stale 로 재사용되지 않게 한다.
        out.add((r["arm"], r["case_id"], r.get("provider", ""), r.get("model", ""),
                 r.get("rail", ""), r.get("split", ""), r.get("dataset_hash", ""),
                 r.get("policy_hash", ""), r.get("rail_artifact_hash", "")))
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
    # ★평가는 fail-loud(R5-#1)★ — 아티팩트 없으면 조용히 HeuristicRail 로 폴백해 결과에 잘못된
    #   레일명을 남기지 않는다. 폴백은 서버·CLI 데모(src/agent/cli.py)에서만 허용. 명시적으로
    #   heuristic 을 원하면 `--rail heuristic`.
    from src.rails.ko_agentguard import KoAgentGuardRail
    try:
        return KoAgentGuardRail()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"ko_agentguard 레일 로드 실패({type(e).__name__}: {e}). 평가는 폴백하지 않습니다. "
            "학습 아티팩트를 받으세요: `python -m scripts.fetch_rail` 또는 "
            "huggingface-cli download cycloevan/safeagent-kr → train/artifacts/. "
            "(휴리스틱으로 실행하려면 명시적으로 --rail heuristic)") from e


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
    ap.add_argument("--provider", default="openai",
                    choices=["openai", "gemini"])   # 오타/미지원 값을 argparse 가 즉시 거부
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--rail", default="ko_agentguard",
                    choices=["ko_agentguard", "heuristic", "kanana", "shieldgemma", "e5"],
                    help="ko_agentguard|heuristic|kanana|shieldgemma|e5 (GPU 레일은 RunPod)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--progress-file", default=str(ROOT / "results" / ".progress.json"),
                    help="진행 heartbeat JSON(scripts.monitor 가 읽음). 빈 문자열이면 비활성")
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
    # 실행 조건 메타 — 결과 행에 각인해 재현/재개 시 조건 혼입을 막는다.
    model = policy["provider"].get(args.provider, {}).get("model", "")
    prov = _provenance(policy, args.split, args.rail)
    # ★run_id 에 계보 지문 포함(R3-6)★ — 데이터·정책이 다른 실행은 run_id 자체가 달라져,
    #   ablation 의 run_id 필터가 신·구 결과를 분리한다(동결 backfill 데이터와 자동 격리).
    _art6 = (prov.get("rail_artifact_hash") or "norail")[:6]
    run_id = (f"{_run_id(args.provider, model, args.rail, args.split)}"
              f".{prov['dataset_hash'][:6]}.{prov['policy_hash'][:6]}.{_art6}")
    # ★요청 레일 vs 실제 레일 분리 기록(R5-#1)★ — 폴백/다른 레일이면 결과에 실제 레일명을 남긴다.
    actual_rail = getattr(rail, "name", type(rail).__name__)
    run_meta = {"provider": args.provider, "model": model, "rail": args.rail,
                "actual_rail": actual_rail, "split": args.split, "run_id": run_id, **prov}
    print(f"계보: dataset={prov['dataset_hash']} policy={prov['policy_hash']} "
          f"commit={prov['commit']} rail_artifact={prov.get('rail_artifact_hash','-')} sdk={prov['sdk']}")
    if actual_rail != args.rail:
        print(f"⚠️ 요청 레일 '{args.rail}' ≠ 실제 '{actual_rail}' — 결과엔 actual_rail 로 기록됨.")
    # 기존 runs.jsonl 에 다른 계보가 섞였으면 경고(코드/데이터 변경 후 stale 재사용 방지).
    _warn_provenance_drift(prov)

    def _key(arm: str, cid: str) -> tuple:
        return (arm, cid, run_meta["provider"], run_meta["model"], run_meta["rail"],
                run_meta["split"], run_meta.get("dataset_hash", ""), run_meta.get("policy_hash", ""),
                run_meta.get("rail_artifact_hash", ""))

    total = len(arms) * (len(attacks) + len(utility))
    from eval.progress import ProgressMonitor
    pm = ProgressMonitor(total, "eval", args.progress_file or None)
    with RUNS.open("a", encoding="utf-8") as f, TRACES.open("a", encoding="utf-8") as tf:
        for arm in arms:
            defenses = ARMS[arm]
            for case in attacks:
                if _key(arm, case["case_id"]) in done:
                    pm.tick(f"{arm}/{case['case_id']} (resume-skip)"); continue
                ep = await _safe_run_case(case, provider, policy, defenses,
                                          rail=rail, customers=CUSTOMERS, arm=arm)
                tf.write(json.dumps({"arm": arm, **ep.to_dict()}, ensure_ascii=False) + "\n"); tf.flush()
                row = {"arm": arm, "kind": "attack", **run_meta, **score_attack(case, ep)}
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
                pm.tick(f"{arm:12s} {case['case_id']}",
                        executed=int(bool(row["executed"])),
                        blocked=int(bool(row["attempted"]) and not row["executed"]),
                        error=int(row["finish_reason"] == "error"))
            for case in utility:
                if _key(arm, case["case_id"]) in done:
                    pm.tick(f"{arm}/{case['case_id']} (resume-skip)"); continue
                ep = await _safe_run_case(case, provider, policy, defenses,
                                          rail=rail, customers=CUSTOMERS, arm=arm)
                tf.write(json.dumps({"arm": arm, **ep.to_dict()}, ensure_ascii=False) + "\n"); tf.flush()
                row = {"arm": arm, "kind": "utility", **run_meta, **score_utility(case, ep)}
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
                pm.tick(f"{arm:12s} {case['case_id']}", util_done=int(bool(row["completed"])))
    pm.done()
    print(f"\n완료. → {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
