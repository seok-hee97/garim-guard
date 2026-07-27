"""탐지기 평가 — Ko-AgentGuard 를 여러 테스트셋 × 난독화에서 채점.

    python -m eval.detector_eval

테스트셋:
  ① Nemotron in-domain 홀드아웃 (영어) — 원본 vs 난독화 (P3 붕괴)
  ② KoTIP 한국어 오염 문서 (교차언어 P6d) — 영어학습 레일이 한국어를 보는가
비교 시스템: ko_agentguard(CPU) / heuristic(한국어 마커) / (RunPod) kanana.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import ROOT
from eval.obfuscate import obfuscate

DATA = ROOT / "data" / "kotip" / "v0.1"
OUT = ROOT / "results" / "detector_multi.json"


def _f1(rail, texts, labels, thr=0.5):
    tp = fp = fn = 0
    for t, y in zip(texts, labels):
        p = 1 if rail.score(t) >= thr else 0
        tp += p and y; fp += p and not y; fn += (not p) and y
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}


def main() -> int:
    from src.rails.ko_agentguard import KoAgentGuardRail
    from src.rails.base import HeuristicRail
    rails = {"ko_agentguard": KoAgentGuardRail(), "heuristic": HeuristicRail()}

    # ① Nemotron 영어 홀드아웃
    nem = [json.loads(l) for l in (DATA / "nemotron_rail.test.jsonl").read_text("utf-8").splitlines() if l.strip()]
    nem_txt = [r["text"] for r in nem]; nem_y = [r["label"] for r in nem]

    # ② KoTIP 한국어 오염 문서(양성) + 정상 KB(음성)
    atk = [json.loads(l) for l in (DATA / "attacks.test.jsonl").read_text("utf-8").splitlines() if l.strip()]
    ko_pos = [a["kb_doc"] for a in atk if a["obfuscation"] == "none"]
    ko_neg = list({a["kb_doc"].split(" [")[0].split(" 환불")[0][:40] for a in atk})  # 정상부 근사
    ko_txt = ko_pos + [a for a in ko_neg]
    ko_y = [1] * len(ko_pos) + [0] * len(ko_neg)

    report = {}
    for name, rail in rails.items():
        r = {"nemotron_en_clean": _f1(rail, nem_txt, nem_y)}
        # 난독화 (양성만 변형)
        for fam in ["base64_wrap", "homoglyph", "jamo_split"]:
            ob = [obfuscate(t, fam) if y == 1 else t for t, y in zip(nem_txt, nem_y)]
            r[f"nemotron_en_{fam}"] = _f1(rail, ob, nem_y)
        r["kotip_ko"] = _f1(rail, ko_txt, ko_y)   # 교차언어
        report[name] = r
        print(f"[{name}] EN clean F1={r['nemotron_en_clean']['f1']} · "
              f"EN base64 F1={r['nemotron_en_base64_wrap']['f1']} · KO F1={r['kotip_ko']['f1']}")

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
