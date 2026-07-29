"""긴 작업 진행 모니터 — rate·ETA·경과·누적 카운트를 콘솔 + heartbeat 파일로 노출.

eval.run 스윕처럼 장시간 도는 작업의 '지금 몇/몇, 처리율, 남은시간, 에러 누계'를 프로세스
밖에서도 볼 수 있게 한다(eval 은 server 와 별프로세스라 in-process 메트릭을 공유 못 함).

    pm = ProgressMonitor(total=98, phase="eval", out_path="results/.progress.json")
    for i, case in enumerate(cases):
        ...
        pm.tick(arm=arm, case_id=cid, executed=+1)   # 콘솔 1줄 + 원자적 heartbeat 쓰기
    pm.done()

heartbeat JSON 은 tmp+os.replace 로 원자적으로 갱신되어 `scripts.monitor --progress-file` 이나
다른 프로세스가 언제든 안전하게 읽을 수 있다. ts 로 staleness(정체) 판정 가능.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class ProgressMonitor:
    def __init__(self, total: int, phase: str = "task", out_path: str | Path | None = None,
                 flush_every_s: float = 1.0) -> None:
        self.total = max(0, int(total))
        self.phase = phase
        self.out_path = Path(out_path) if out_path else None
        self.flush_every_s = flush_every_s
        self.done_count = 0
        self.counts: dict[str, int] = {}
        self._started = time.perf_counter()
        self._started_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._last_flush = 0.0
        self._ema_rate = 0.0            # 지수이동평균 처리율(건/s) — 튀는 케이스 완화
        self._last_tick_t = self._started

    def tick(self, label: str = "", **counts: int) -> None:
        self.done_count += 1
        now = time.perf_counter()
        dt = max(1e-6, now - self._last_tick_t)
        inst = 1.0 / dt
        self._ema_rate = inst if self._ema_rate == 0 else 0.3 * inst + 0.7 * self._ema_rate
        self._last_tick_t = now
        for k, v in counts.items():
            self.counts[k] = self.counts.get(k, 0) + int(v)
        elapsed = now - self._started
        remaining = max(0, self.total - self.done_count)
        eta = remaining / self._ema_rate if self._ema_rate > 0 else 0.0
        pct = (100.0 * self.done_count / self.total) if self.total else 0.0
        print(f"[{self.done_count}/{self.total}] {pct:4.0f}%  "
              f"rate={self._ema_rate:4.1f}/s  ETA={_fmt(eta)}  경과={_fmt(elapsed)}  "
              f"{label}  {_counts_str(self.counts)}", flush=True)
        if self.out_path is not None and (now - self._last_flush) >= self.flush_every_s:
            self._flush(eta, elapsed, pct, label)
            self._last_flush = now

    def done(self) -> None:
        if self.out_path is not None:
            elapsed = time.perf_counter() - self._started
            self._flush(0.0, elapsed, 100.0 if self.total else 0.0, "done")

    def _flush(self, eta: float, elapsed: float, pct: float, label: str) -> None:
        snap: dict[str, Any] = {
            "phase": self.phase, "done": self.done_count, "total": self.total,
            "pct": round(pct, 1), "rate_per_s": round(self._ema_rate, 2),
            "eta_s": round(eta, 1), "elapsed_s": round(elapsed, 1),
            "started_at": self._started_wall,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "current": label, "counts": dict(self.counts),
        }
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out_path.with_suffix(self.out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.out_path)     # 원자적 교체 — 읽는 쪽이 부분쓰기를 보지 않음


def _fmt(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m{s%60:02d}s"
    return f"{s//3600}h{(s%3600)//60:02d}m"


def _counts_str(counts: dict[str, int]) -> str:
    return " ".join(f"{k}={v}" for k, v in counts.items())
