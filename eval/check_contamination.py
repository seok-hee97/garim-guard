"""train/test 오염 감사 — Ko-AgentGuard 레일 학습셋의 근사중복 누수 탐지.

Nemotron projector 는 environment 템플릿이 반복되므로 domain-grouped split 을 쓰지만,
그래도 train/test 간 근사중복이 있으면 F1 이 부풀려진다. 5-gram Jaccard 로 감사한다.

    python -m eval.check_contamination        # green/경고

기준(사전등록 계열): 모든 (train,test) 최근접 쌍의 5-gram Jaccard < 0.60. 초과 쌍을 보고.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import ROOT

DATA = ROOT / "data" / "kotip" / "v0.1"
THRESHOLD = 0.60


def _load(split: str) -> list[dict]:
    p = DATA / f"nemotron_rail.{split}.jsonl"
    return [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]


def _ngrams(text: str, n: int = 5) -> set[str]:
    toks = text.split()
    return {" ".join(toks[i:i + n]) for i in range(max(0, len(toks) - n + 1))} or {text}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    train, test = _load("train"), _load("test")
    # domain 분리 확인 (grouped split 의 핵심)
    tr_dom = {r.get("domain") for r in train}
    te_dom = {r.get("domain") for r in test}
    overlap_dom = tr_dom & te_dom
    print(f"train domains: {sorted(d for d in tr_dom if d)}")
    print(f"test domains:  {sorted(d for d in te_dom if d)}")
    print(f"도메인 겹침: {sorted(d for d in overlap_dom if d) or '없음 ✅ (grouped split 정상)'}")

    # 근사중복: 각 test 항목의 최근접 train Jaccard
    tr_ng = [_ngrams(r["text"]) for r in train]
    worst = 0.0
    n_over = 0
    for r in test:
        tg = _ngrams(r["text"])
        best = max((_jaccard(tg, g) for g in tr_ng), default=0.0)
        worst = max(worst, best)
        if best >= THRESHOLD:
            n_over += 1

    print(f"\ntrain {len(train)} · test {len(test)}")
    print(f"최대 (test,train) 5-gram Jaccard = {worst:.3f} (기준 <{THRESHOLD})")
    print(f"기준 초과 test 항목: {n_over}건")
    ok = not overlap_dom and n_over == 0
    print(f"\n오염 감사: {'✅ GREEN (누수 없음)' if ok else '⚠️ 검토 필요'}")

    out = ROOT / "results" / "contamination.json"
    out.write_text(json.dumps({
        "domain_overlap": sorted(d for d in overlap_dom if d),
        "max_jaccard": round(worst, 3), "n_over_threshold": n_over,
        "threshold": THRESHOLD, "green": ok,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
