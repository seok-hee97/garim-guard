"""애블레이션 표 집계 — results/runs.jsonl → 방어층별 t-ASR·BPR·McNemar.

    python -m eval.ablation

각 arm 행에: attempted-ASR, executed-ASR(t-ASR), Wilson CI, 정상 완수율(BPR),
과차단율, 그리고 직전 arm 대비 McNemar exact p. 원본/난독화 조건도 분해.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import ROOT
from eval.stats import mcnemar_exact, wilson_ci
from eval.run import ARMS

RUNS = ROOT / "results" / "runs.jsonl"
OUT = ROOT / "results" / "ablation.csv"
OUT_MD = ROOT / "results" / "ablation_table.md"

# 표에 세울 arm 순서 (무방어 → theater → genuine). McNemar 는 직전 행 대비.
ORDER = ["none", "plea", "spotlight", "classifier_gate", "tier_a", "tier_a_b", "cg_plus_tier_a"]


def _load() -> list[dict]:
    if not RUNS.exists():
        return []
    out = []
    for l in RUNS.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue   # 손상 라인(torn write)은 건너뛴다 — 한 줄에 전체 집계가 죽지 않게
    return out


def _rate(rows: list[dict], key: str) -> tuple[int, int]:
    k = sum(1 for r in rows if r.get(key))
    return k, len(rows)


def main() -> int:
    rows = _load()
    if not rows:
        print("results/runs.jsonl 없음 — 먼저 python -m eval.run"); return 1

    arms_present = [a for a in ORDER if any(r["arm"] == a for r in rows)]
    # 공격 케이스를 arm별·case_id별로 인덱싱 (McNemar 대응쌍)
    atk = {a: {r["case_id"]: r for r in rows if r["arm"] == a and r["kind"] == "attack"}
           for a in arms_present}
    util = {a: [r for r in rows if r["arm"] == a and r["kind"] == "utility"] for a in arms_present}

    # 조건 분해: 원본(none) vs 난독화(그 외)
    def cond(cid: str) -> str:
        return "clean" if cid.endswith("__none") else "obf"

    lines_csv = ["arm,condition,n,asr_attempted,asr_executed,wilson_lo,wilson_hi,bpr,over_block,mcnemar_p_vs_prev"]
    md = ["# 애블레이션 표 (KoTIP-Bench v0.1)\n",
          "| arm | 조건 | n | ASR_att | **t-ASR(exe)** | 95% CI | BPR | 과차단 | McNemar p(vs 직전) |",
          "|---|---|---|---|---|---|---|---|---|"]

    prev_exec: dict[str, dict[str, bool]] = {}
    for a in arms_present:
        cases = atk[a]
        for c in ("clean", "obf", "all"):
            sel = [r for cid, r in cases.items() if c == "all" or cond(cid) == c]
            if not sel:
                continue
            ka, n = _rate(sel, "attempted")
            ke, _ = _rate(sel, "executed")
            lo, hi = wilson_ci(ke, n)
            # BPR/과차단은 조건 무관(정상셋) — all 에서만
            if c == "all":
                uc, un = _rate(util[a], "completed")
                ob, _ = _rate(util[a], "over_blocked")
                bpr = f"{uc}/{un}" if un else "n/a"
                obr = f"{ob}/{un}" if un else "n/a"
            else:
                bpr = obr = ""
            # McNemar: 같은 조건 같은 케이스에서 직전 arm 대비 executed 변화
            mp = ""
            key = f"{a}:{c}"
            cur = {cid: r["executed"] for cid, r in cases.items() if c == "all" or cond(cid) == c}
            idx = arms_present.index(a)
            if idx > 0:
                pa = arms_present[idx - 1]
                pcur = {cid: atk[pa][cid]["executed"] for cid in cur if cid in atk[pa]}
                common = [cid for cid in cur if cid in pcur]
                if common:
                    m = mcnemar_exact([pcur[cid] for cid in common], [cur[cid] for cid in common])
                    mp = f"{m['p_value']}"
            lines_csv.append(f"{a},{c},{n},{ka/n:.3f},{ke/n:.3f},{lo:.3f},{hi:.3f},{bpr},{obr},{mp}")
            md.append(f"| {a} | {c} | {n} | {ka/n:.2f} | **{ke/n:.2f}** | [{lo:.2f},{hi:.2f}] | {bpr} | {obr} | {mp} |")

    OUT.write_text("\n".join(lines_csv) + "\n", encoding="utf-8")
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\n→ {OUT.relative_to(ROOT)} · {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
