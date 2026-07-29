"""터미널 라이브 모니터 — '지금 무엇이 실행되고 진행 중인가'를 한 화면에.

    python -m scripts.monitor                       # http://localhost:8000/v1/metrics 폴링(3s)
    python -m scripts.monitor --once                # 단발 스냅샷
    python -m scripts.monitor --progress-file results/.progress.json   # eval 진행바 동시 표시

의존성 0(urllib+json). 서버 미가동/연결실패는 'server down' 으로 우아하게 표시(크래시 금지).
HTTP 서비스의 GET /v1/metrics(누적 카운터·in-flight·지연 분위수)와 eval heartbeat 파일을 읽어
카운터 표·in-flight·leak 경보(0=초록/≥1=빨강)·진행률(rate/ETA)을 주기적으로 재그린다. ★PII 없음★.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

_G, _R, _Y, _B, _DIM, _X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"


def _fetch(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 — 로컬 관측 URL
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def _read_progress(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _table(title: str, series: dict) -> list[str]:
    lines = [f"{_DIM}{title}{_X}"]
    if not series:
        return lines + [f"  {_DIM}(없음){_X}"]
    for k, v in sorted(series.items()):
        lines.append(f"  {v:>6}  {k}")
    return lines


def _render(metrics: dict | None, progress: dict | None, url: str) -> str:
    out: list[str] = []
    out.append(f"{_B}SafeAgent-KR 라이브 모니터{_X}  {_DIM}{url}  {time.strftime('%H:%M:%S')}{_X}")
    out.append("─" * 68)
    if metrics is None:
        out.append(f"{_Y}● server down / 연결 실패{_X} — uvicorn src.agent.server:app 확인")
    else:
        b = metrics.get("build", {})
        inf = metrics.get("in_flight", {})
        ready = metrics.get("ready")
        rc = _G if ready else _Y
        out.append(f"provider={b.get('provider','?')} model={b.get('model','?')} rail={b.get('rail','?')}"
                   f"  ready={rc}{ready}{_X}  uptime={metrics.get('uptime_s',0)}s")
        out.append(f"in-flight: episodes={_B}{inf.get('episodes',0)}{_X}  idem={inf.get('idem',0)}")
        counters = metrics.get("counters", {})
        # leak 경보(critical 불변식)
        leaks = counters.get("leaks_total") or counters.get("tool_executed_total", {})
        out.append("")
        out += _table("episodes_total (finish_reason)", counters.get("episodes_total", {}))
        out += _table("gate_blocks_total (rule)", counters.get("gate_blocks_total", {}))
        out += _table("dlp_redactions_total (entity)", counters.get("dlp_redactions_total", {}))
        out += _table("errors_total (stage)", counters.get("errors_total", {}))
        out += _table("http_requests_total (method,route,status)", counters.get("http_requests_total", {}))
        lat = metrics.get("histograms", {}).get("episode_latency_ms", {}).get("_", {})
        if lat:
            out.append(f"{_DIM}episode_latency{_X}  p50={lat.get('p50_ms')}ms p95={lat.get('p95_ms')}ms n={lat.get('count')}")
        # critical leak 경보
        leaks_total = counters.get("leaks_total", {})
        if leaks_total:
            out.append(f"{_R}⚠ LEAK 경보 leaks_total={leaks_total}{_X}")
    if progress is not None:
        out.append("─" * 68)
        pct = progress.get("pct", 0)
        bar_n = int(pct / 5)
        bar = "█" * bar_n + "░" * (20 - bar_n)
        out.append(f"{_B}작업 진행{_X} [{progress.get('phase','?')}]  {bar} {pct:.0f}%")
        out.append(f"  {progress.get('done',0)}/{progress.get('total',0)}  "
                   f"rate={progress.get('rate_per_s',0)}/s  ETA={progress.get('eta_s',0)}s  "
                   f"경과={progress.get('elapsed_s',0)}s")
        if progress.get("counts"):
            out.append(f"  누적: {progress['counts']}")
        out.append(f"  {_DIM}현재: {progress.get('current','')}  (ts={progress.get('ts','')}){_X}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--once", action="store_true", help="단발 스냅샷 후 종료")
    ap.add_argument("--progress-file", default="results/.progress.json")
    args = ap.parse_args()
    murl = args.url.rstrip("/") + "/v1/metrics"
    try:
        while True:
            m = _fetch(murl)
            p = _read_progress(args.progress_file)
            if not args.once:
                print("\033[2J\033[H", end="")     # 화면 클리어 + 커서 홈
            print(_render(m, p, murl))
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
