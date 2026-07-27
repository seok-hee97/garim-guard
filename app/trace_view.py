"""정적 HTML 트레이스 뷰어 — Streamlit 대체(컷). 저장된 트레이스/결과를 렌더.

    python -m app.trace_view          # results/runs.jsonl → report/trace_view.html

평가적 내용은 트레이스에 있지 위젯에 있지 않다는 원칙(계획 §10.1). 의존성 0, 정적 파일.
seam별 도구호출·차단 지점(gate_rule)·attempted/executed·레일 점수를 한 페이지에 렌더한다.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "results" / "runs.jsonl"
ABL = ROOT / "results" / "ablation_table.md"
OUT = ROOT / "report" / "trace_view.html"

_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif;margin:2rem;
  background:#0d1117;color:#c9d1d9;line-height:1.5}
h1,h2{color:#e6edf3}
.row{border:1px solid #30363d;border-radius:8px;padding:.6rem 1rem;margin:.4rem 0}
.blk{border-left:4px solid #f85149} .exe{border-left:4px solid #f85149;background:#2d1214}
.ok{border-left:4px solid #3fb950}
.tag{display:inline-block;padding:.05rem .5rem;border-radius:10px;font-size:.8rem;margin-right:.4rem}
.t-blk{background:#5a1e22;color:#ffb4b4} .t-exe{background:#7a1620;color:#fff}
.t-ok{background:#1a3a22;color:#7ee787} .t-arm{background:#1f2d3d;color:#79c0ff}
code{background:#161b22;padding:.1rem .3rem;border-radius:4px}
table{border-collapse:collapse;margin:1rem 0}
td,th{border:1px solid #30363d;padding:.3rem .6rem}
.mono{font-family:ui-monospace,monospace;font-size:.85rem;color:#8b949e}
"""


def _rows():
    if not RUNS.exists():
        return []
    return [json.loads(l) for l in RUNS.read_text("utf-8").splitlines() if l.strip()]


def main() -> int:
    rows = _rows()
    attacks = [r for r in rows if r.get("kind") == "attack"]
    parts = [f"<style>{_CSS}</style>", "<h1>SafeAgent-KR 트레이스 뷰어</h1>"]

    # 애블레이션 표 (있으면 그대로 삽입)
    if ABL.exists():
        parts.append("<h2>애블레이션 표</h2><pre class='mono'>"
                     + html.escape(ABL.read_text("utf-8")) + "</pre>")

    # arm별 공격 결과 요약
    parts.append("<h2>공격 케이스 결과 (arm별)</h2>")
    by_arm: dict[str, list] = {}
    for r in attacks:
        by_arm.setdefault(r["arm"], []).append(r)
    for arm, items in by_arm.items():
        att = sum(1 for x in items if x.get("attempted"))
        exe = sum(1 for x in items if x.get("executed"))
        parts.append(f"<h3><span class='tag t-arm'>{html.escape(arm)}</span> "
                     f"attempted {att}/{len(items)} · executed(t-ASR) {exe}/{len(items)}</h3>")
        for r in items:
            if not (r.get("attempted") or r.get("executed")):
                continue  # 발동한 것만 표시
            cls = "exe" if r.get("executed") else "blk"
            tag = ("<span class='tag t-exe'>🔴 실행(유출)</span>" if r.get("executed")
                   else "<span class='tag t-blk'>🛑 차단</span>")
            rule = html.escape(", ".join(r.get("blocked_rules", []) or []))
            parts.append(
                f"<div class='row {cls}'>{tag}<code>{html.escape(r['case_id'])}</code> "
                f"<span class='mono'>차단규칙: {rule or '-'} · rail_max={r.get('rail_max',0):.2f}</span></div>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("<!doctype html><meta charset='utf-8'>" + "\n".join(parts), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)} (브라우저로 열기)")
    print(f"   공격 {len(attacks)}행, arm {len(by_arm)}종 렌더")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
