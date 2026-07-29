"""정적 HTML 트레이스 뷰어 — Streamlit 대체(컷). 저장된 트레이스/결과를 렌더.

    python -m app.trace_view                       # 기본 입력 → report/trace_view.html
    python -m app.trace_view --traces results/demo_traces.jsonl --out /tmp/v.html

두 종류를 한 페이지에 렌더한다:
 1. ★에피소드 실행 과정 타임라인★ — hop 단위(입력 · 도구호출 순서 · before_tool 게이트 결정·
    차단규칙 · attempted/executed · after_tool 모델 전달값 · 레일 점수). demo_traces.jsonl이
    있으면, 무방어→유출 / 게이트→차단을 seam 흐름으로 나란히 보인다(발표/녹화용).
 2. 애블레이션 집계 — arm별 t-ASR 표 + 케이스별 차단/실행 스코어카드(runs.jsonl).

평가적 내용은 트레이스에 있지 위젯에 있지 않다는 원칙(계획 §10.1). 의존성 0, 정적 파일.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "results" / "runs.jsonl"
ABL = ROOT / "results" / "ablation_table.md"
DEMO_TRACES = ROOT / "results" / "demo_traces.jsonl"
OUT = ROOT / "report" / "trace_view.html"

_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif;margin:2rem;
  background:#0d1117;color:#c9d1d9;line-height:1.5}
h1,h2,h3{color:#e6edf3}
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
/* 실행 과정 타임라인 */
.epgrid{display:flex;flex-wrap:wrap;gap:1rem;margin:1rem 0}
.ep{flex:1;min-width:340px;border:1px solid #30363d;border-radius:10px;padding:.8rem 1rem;background:#11161d}
.ephead{font-weight:700;color:#e6edf3;margin-bottom:.5rem}
/* ★배지 잘림 방지★ — 홉 행은 [메타 고정폭][인자 가변][배지 고정폭] 3분할이다.
   badge 는 절대 줄지 않고(flex:0 0 auto) 인자 셀만 줄어들며(min-width:0) 줄바꿈한다.
   min-width:0 이 없으면 flex 아이템이 콘텐츠 폭 밑으로 못 줄어 배지가 카드 밖으로 밀려 잘린다. */
.hop{display:flex;align-items:center;gap:.5rem;padding:.35rem .5rem;margin:.3rem 0;
  border-radius:6px;border-left:4px solid #30363d;background:#0d1117;min-width:0}
.hop.pass{border-left-color:#3fb950} .hop.block{border-left-color:#f85149;background:#2d1214}
.step{font-family:ui-monospace,monospace;color:#8b949e;font-size:.8rem;flex:0 0 2.4rem}
.tool{font-weight:600;color:#e6edf3;flex:0 0 auto}
.args{color:#8b949e;font-size:.82rem;flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.seam{color:#79c0ff;font-size:.72rem;flex:0 0 auto}
.badge{margin-left:auto;font-size:.78rem;padding:.05rem .5rem;border-radius:8px;
  white-space:nowrap;flex:0 0 auto}
.b-pass{background:#1a3a22;color:#7ee787} .b-block{background:#7a1620;color:#fff}
.verdict{margin-top:.5rem;font-weight:600}
.leak{color:#ff9b9b} .safe{color:#7ee787}
.inputbox,.detail{font-family:ui-monospace,monospace;font-size:.76rem;color:#a9bed3;
  background:#0a1320;border:1px solid #24364a;border-radius:6px;padding:.4rem .55rem;margin:.3rem 0;
  overflow-wrap:anywhere}
.detail{margin:.1rem 0 .45rem 2.9rem;border-left:3px solid #79c0ff}
"""


def _load(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return os.path.relpath(str(p), str(ROOT))


def _args_str(d: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in (d or {}).items())


def _episode_timelines(traces: list[dict]) -> list[str]:
    """★실행 과정★ — 에피소드별 hop 타임라인(도구호출 순서·게이트 차단지점)."""
    if not traces:
        return []
    parts = ["<h2>① 에피소드 실행 과정 (seam 흐름 · 게이트 차단지점)</h2>",
             "<p class='mono'>같은 공격 에피소드를 arm별로 재생 — before_tool 게이트만 "
             "실행(executed) 결과를 바꾼다. 🛑=차단(gate_rule), ✅=실행.</p>",
             "<div class='epgrid'>"]
    for ep in traces:
        tool_hops = [h for h in ep.get("hops", []) if h.get("tool_name")]
        blocked = [h for h in tool_hops if not h.get("executed") and h.get("attempted")]
        parts.append("<div class='ep'>")
        parts.append(f"<div class='ephead'>{html.escape(str(ep.get('scenario','episode')))}"
                     f" · finish={html.escape(str(ep.get('finish_reason','')))}"
                     f" · {len(tool_hops)} hops</div>")
        if ep.get("user_input"):
            parts.append("<div class='inputbox'>INPUT → "
                         + html.escape(str(ep.get("user_input"))) + "</div>")
        for h in tool_hops:
            passed = h.get("executed")
            cls = "pass" if passed else "block"
            if passed:
                badge = "<span class='badge b-pass'>✅ 실행 (ALLOW)</span>"
            else:
                badge = ("<span class='badge b-block'>🛑 차단 · "
                         + html.escape(str(h.get("gate_rule") or h.get("gate_decision", "BLOCK")))
                         + "</span>")
            parts.append(
                f"<div class='hop {cls}'>"
                f"<span class='step'>hop{h.get('hop')}</span>"
                f"<span class='seam'>before_tool▸</span>"
                f"<span class='tool'>{html.escape(str(h.get('tool_name')))}</span>"
                f"<span class='args'>({html.escape(_args_str(h.get('tool_args', {})))})</span>"
                f"{badge}</div>")
            detail = []
            if h.get("tool_result_preview") is not None:
                detail.append("after_tool→model: " + json.dumps(
                    h.get("tool_result_preview"), ensure_ascii=False, default=str))
            if h.get("rail_scores"):
                detail.append("rail[LOG-ONLY]: " + json.dumps(
                    h.get("rail_scores"), ensure_ascii=False, default=str))
            if h.get("note"):
                detail.append("note: " + str(h.get("note")))
            if detail:
                parts.append("<div class='detail'>" + "<br>".join(
                    html.escape(x) for x in detail) + "</div>")
        # 유출 판정
        leaked = any(h.get("tool_name") == "send_email" and h.get("executed")
                     for h in tool_hops)
        attempted_exfil = any(h.get("tool_name") == "send_email" and h.get("attempted")
                              for h in tool_hops)
        if leaked:
            v = "<span class='leak'>🔴 공격자 이메일 유출 실행됨</span>"
        elif attempted_exfil:
            v = "<span class='safe'>🛑 유출 시도됨 · 게이트가 실행 차단 (attempted=True, executed=False)</span>"
        else:
            v = "<span class='mono'>· 유출 시도 없음</span>"
        parts.append(f"<div class='verdict'>{v}</div>")
        if blocked:
            rules = ", ".join(sorted({str(h.get("gate_rule") or h.get("gate_decision")) for h in blocked}))
            parts.append(f"<div class='mono'>차단 규칙: {html.escape(rules)}</div>")
        parts.append("</div>")
    parts.append("</div>")
    return parts


def _ablation_summary(rows: list[dict]) -> list[str]:
    attacks = [r for r in rows if r.get("kind") == "attack"]
    parts = []
    if ABL.exists():
        parts.append("<h2>② 애블레이션 표</h2><pre class='mono'>"
                     + html.escape(ABL.read_text("utf-8")) + "</pre>")
    if not attacks:
        return parts
    parts.append("<h2>③ 공격 케이스 결과 (arm별 스코어카드)</h2>")
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
                continue
            cls = "exe" if r.get("executed") else "blk"
            tag = ("<span class='tag t-exe'>🔴 실행(유출)</span>" if r.get("executed")
                   else "<span class='tag t-blk'>🛑 차단</span>")
            rule = html.escape(", ".join(r.get("blocked_rules", []) or []))
            parts.append(
                f"<div class='row {cls}'>{tag}<code>{html.escape(r['case_id'])}</code> "
                f"<span class='mono'>차단규칙: {rule or '-'} · rail_max={r.get('rail_max',0):.2f}</span></div>")
    return parts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default=str(DEMO_TRACES), help="에피소드 hop 트레이스 JSONL")
    ap.add_argument("--runs", default=str(RUNS), help="애블레이션 집계 JSONL")
    ap.add_argument("--out", default=str(OUT), help="출력 HTML 경로")
    ap.add_argument("--sections", choices=("all", "timeline"), default="all",
                    help="timeline = ① 실행과정만(슬라이드 캡처용 — 표가 프레임에 걸리지 않음)")
    args = ap.parse_args()

    full = args.sections == "all"
    traces = _load(Path(args.traces))
    rows = _load(Path(args.runs)) if full else []
    parts = [f"<style>{_CSS}</style>"]
    if full:
        parts.append("<h1>SafeAgent-KR 트레이스 뷰어</h1>")
    parts += _episode_timelines(traces)
    if full:                       # timeline 모드는 ②③ 를 통째로 생략(캡처 프레임 보호)
        parts += _ablation_summary(rows)
    if not traces and not rows:
        parts.append("<p>표시할 트레이스가 없습니다. <code>python -m scripts.run_demo</code> "
                     "또는 <code>python -m eval.run</code> 먼저 실행하세요.</p>")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<!doctype html><meta charset='utf-8'>" + "\n".join(parts), encoding="utf-8")
    print(f"→ {_rel(out)} (브라우저로 열기)")
    print(f"   에피소드 {len(traces)}개 타임라인, 공격 {sum(1 for r in rows if r.get('kind')=='attack')}행 렌더")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
