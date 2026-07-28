"""results/runs.jsonl 소급 메타데이터 백필 — 프로즌 헤드라인 런의 조건을 각 행에 각인.

    python -m scripts.backfill_run_meta          # 미리보기(변경 안 함)
    python -m scripts.backfill_run_meta --write   # 실제 기록(백업 .bak 생성)

배경: run_meta(provider/model/rail/split/run_id)는 나중에 eval/run.py 에 추가됐다.
그 이전에 생성된 프로즌 310행에는 이 필드가 없어 재현/격리 추적이 불가능하다.
헤드라인 런 조건은 results/RESULTS.md:3-5 에 문서화돼 있다:
    provider=openai · model=gpt-4o-mini(temp=0) · rail=ko_agentguard · split=test

★ 추정이 아니라 문서화된 사실을 각인한다. 이미 메타가 있는 행은 건드리지 않는다(멱등).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import ROOT

RUNS = ROOT / "results" / "runs.jsonl"

# RESULTS.md:3-5 에 문서화된 프로즌 헤드라인 런 조건.
FROZEN_META = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "rail": "ko_agentguard",
    "split": "test",
}
FROZEN_META["run_id"] = f"{FROZEN_META['split']}.{FROZEN_META['provider']}.{FROZEN_META['model']}.{FROZEN_META['rail']}"
# ★출처 표기(리뷰)★ — 이 메타는 런타임 provenance 가 아니라 문서화된 조건에서 소급/재구성됐다.
#   provenance(dataset_hash/commit) 가 있는 새 run.py 산출과 구분된다.
FROZEN_META["metadata_source"] = "backfilled_from_docs"

META_KEYS = ("provider", "model", "rail", "split", "run_id", "metadata_source")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="실제로 파일에 기록(기본: 미리보기)")
    args = ap.parse_args()

    if not RUNS.exists():
        print(f"{RUNS} 없음"); return 1

    lines = [l for l in RUNS.read_text("utf-8").splitlines() if l.strip()]
    out, filled, already = [], 0, 0
    for l in lines:
        r = json.loads(l)
        missing = [k for k in META_KEYS if k not in r]
        if not missing:
            already += 1
        else:
            for k in missing:
                r[k] = FROZEN_META[k]
            filled += 1
        out.append(json.dumps(r, ensure_ascii=False))

    print(f"총 {len(lines)}행 · 백필 {filled} · 이미완비 {already}")
    print(f"각인 조건: {FROZEN_META}")
    if not args.write:
        print("→ 미리보기만. 실제 기록하려면 --write")
        return 0

    RUNS.with_suffix(".jsonl.bak").write_text("\n".join(lines) + "\n", encoding="utf-8")
    RUNS.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✅ 기록 완료(백업: {RUNS.name}.bak)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
