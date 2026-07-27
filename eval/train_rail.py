"""Ko-AgentGuard 레일 학습 + 평가 (CPU, sklearn).

    python -m eval.train_rail            # 학습 + 홀드아웃 평가 + 난독화 붕괴 측정

학습: data/kotip/v0.1/nemotron_rail.train.jsonl (domain-grouped, 라운드트립 세탁 없는 원문)
평가: nemotron_rail.test.jsonl (in-domain 홀드아웃) + 난독화 변형(P3 붕괴 측정)
산출: train/artifacts/ko-agentguard-lr.joblib + results/detector_eval.json

⚠️ 이 데이터는 영어(Nemotron)다. 한국어 도구채널 평가(MAPS/네이티브 골드)는 RunPod
   단계에서 e5-small 로 교차언어 전이를 잰다. 지금은 CPU 로 '탐지기가 난독화에 붕괴하는가'
   (P3)를 in-domain 에서 먼저 확립한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "kotip" / "v0.1"
ARTIFACT = ROOT / "train" / "artifacts" / "ko-agentguard-lr.joblib"
REPORT = ROOT / "results" / "detector_eval.json"


def _load(split: str) -> tuple[list[str], list[int]]:
    p = DATA / f"nemotron_rail.{split}.jsonl"
    texts, labels = [], []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            texts.append(r["text"])
            labels.append(int(r["label"]))
    return texts, labels


def _f1_at(pipe, texts: list[str], labels: list[int], thr: float) -> dict:
    from sklearn.metrics import precision_recall_fscore_support
    proba = pipe.predict_proba(texts)[:, 1]
    pred = [1 if p >= thr else 0 for p in proba]
    p, r, f, _ = precision_recall_fscore_support(labels, pred, average="binary", zero_division=0)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "n": len(labels)}


def _load_kotip_ko():
    """KoTIP 한국어 오염 문서(양성) + 정상부(음성) — 교차언어 평가용."""
    import json
    from pathlib import Path
    p = ROOT / "data" / "kotip" / "v0.1" / "attacks.test.jsonl"
    atk = [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]
    pos = [a["kb_doc"] for a in atk if a["obfuscation"] == "none"]
    neg = list({a["kb_doc"].split(" [")[0].split(" 환불")[0][:40] for a in atk})
    return pos + neg, [1] * len(pos) + [0] * len(neg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--encoder", default="tfidf", choices=["tfidf", "e5"])
    args = ap.parse_args()

    from eval.obfuscate import obfuscate

    Xtr, ytr = _load("train")
    Xte, yte = _load("test")
    print(f"[{args.encoder}] 학습 {len(Xtr)} · 테스트 {len(Xte)}")

    if args.encoder == "e5":
        # 프리즈 e5-small + LR head. TF-IDF 와 달리 다국어라 한국어 교차언어 전이 기대.
        from src.rails.e5_lr import E5LRRail
        rail = E5LRRail(); rail.fit(Xtr, ytr)
        class _P:  # pipe 인터페이스 어댑터
            def predict_proba(self, texts):
                import numpy as np
                pr = rail.predict_proba(texts)
                return np.array([[1 - p, p] for p in pr])
        pipe = _P()
        # ★ 교차언어: TF-IDF 가 0.0 이던 한국어 KoTIP 에서 e5 는 잡는가
        ko_x, ko_y = _load_kotip_ko()
        ko = _f1_at(pipe, ko_x, ko_y, args.threshold)
        print(f"교차언어(KoTIP 한국어) F1={ko['f1']} R={ko['recall']}  (TF-IDF 는 0.0 이었음)")
    else:
        from src.rails.ko_agentguard import build_pipeline
        pipe = build_pipeline()
        pipe.fit(Xtr, ytr)

    # in-domain 홀드아웃
    clean = _f1_at(pipe, Xte, yte, args.threshold)
    print(f"in-domain(clean)   F1={clean['f1']} P={clean['precision']} R={clean['recall']}")

    # ── P3: 난독화 붕괴 측정 (양성만 변형, 음성은 그대로) ──
    families = ["zero_width", "homoglyph", "base64_wrap", "jamo_split"]
    collapse = {}
    for fam in families:
        Xob = [obfuscate(t, fam) if y == 1 else t for t, y in zip(Xte, yte)]
        m = _f1_at(pipe, Xob, yte, args.threshold)
        collapse[fam] = m
        drop = round(clean["recall"] - m["recall"], 4)
        print(f"  {fam:12s} F1={m['f1']} R={m['recall']}  (Δrecall={drop:+.4f})")

    worst = min(collapse.values(), key=lambda m: m["f1"])
    p3_drop = round(clean["f1"] - worst["f1"], 4)
    print(f"\nP3 붕괴폭(clean F1 − 최악 난독화 F1) = {p3_drop}  (사전등록 목표 ≥ 0.25)")

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump(pipe, ARTIFACT)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({
        "threshold": args.threshold, "clean": clean, "obfuscated": collapse,
        "p3_collapse": p3_drop, "note": "영어 in-domain. 한국어 교차언어는 RunPod e5-small 단계.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n아티팩트: {ARTIFACT.relative_to(ROOT)}\n리포트: {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
