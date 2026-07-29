"""in-process 메트릭 레지스트리 — '지금까지 무엇이 얼마나 실행/차단/유출됐고 얼마나 걸렸나'.

로그/JSONL 을 파싱하지 않고 O(1) 로 집계 상태를 답한다. `GET /v1/metrics`·`scripts.monitor`·
`run_demo` 요약이 이 레지스트리를 읽는다. ★PII 는 구조적으로 담기지 않는다★ — 카운터/게이지/
히스토그램은 정수와 **enum/정책키 라벨만** 저장한다(원본 값·이메일·계좌·principal.id·episode_id
금지). log.py 규율(원본 PII 미로깅)과 policy.yaml log_mappings:false 를 그대로 상속한다.

    from src.agent import metrics as M
    M.inc("gate_decisions_total", decision="BLOCK", rule="egress.email_domains")
    M.observe("episode_latency_ms", 1234.0, )
    M.inc_gauge("episodes_in_flight"); ... ; M.dec_gauge("episodes_in_flight")
    snap = M.snapshot()   # {"counters":..., "gauges":..., "histograms":{name:{p50,p95,count,...}}}

동시성: 증분이 이벤트루프 스레드·asyncio.to_thread 워커·eval 메인 스레드에서 모두 들어오므로
threading.Lock(OS 스레드 락)으로 감싼다. 증분은 락 안에서 즉시 끝나 경합은 무시 가능하다.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 지연 히스토그램 고정 버킷(ms) — 시리즈당 유한 메모리(~15 int). 상한은 +Inf 로 처리.
BUCKETS_MS: tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000,
                                 10000, 30000, 60000, float("inf"))
_MAX_SERIES = 256          # metric 당 시리즈 상한 — 라벨 폭발/PII 오염 fail-safe

_lock = threading.Lock()
_counters: dict[str, dict[tuple, int]] = {}
_gauges: dict[str, dict[tuple, float]] = {}
_hist: dict[str, dict[tuple, dict[str, Any]]] = {}
_dropped: dict[str, int] = {}          # metrics_dropped_total{metric} — 상한 초과로 버린 시리즈
_STARTED_AT = time.time()


def _key(labels: dict[str, Any]) -> tuple:
    """라벨 dict → 정렬된 (k,v) 튜플. 값은 str 로 강제(정수/enum 만 들어오도록 호출측 규율)."""
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _guard(store: dict[str, dict], name: str, key: tuple) -> bool:
    """시리즈 상한 초과 시 False(드롭). 무한 성장·라벨 폭발 방지."""
    series = store.setdefault(name, {})
    if key not in series and len(series) >= _MAX_SERIES:
        _dropped[name] = _dropped.get(name, 0) + 1
        return False
    return True


def inc(name: str, n: int = 1, **labels: Any) -> None:
    key = _key(labels)
    with _lock:
        if not _guard(_counters, name, key):
            return
        _counters[name][key] = _counters[name].get(key, 0) + n


def observe(name: str, value_ms: float, **labels: Any) -> None:
    """지연(ms) 히스토그램 관측 — 버킷/합/개수 누적."""
    key = _key(labels)
    with _lock:
        if not _guard(_hist, name, key):
            return
        h = _hist[name].get(key)
        if h is None:
            h = {"buckets": [0] * len(BUCKETS_MS), "sum": 0.0, "count": 0}
            _hist[name][key] = h
        h["sum"] += float(value_ms)
        h["count"] += 1
        for i, edge in enumerate(BUCKETS_MS):
            if value_ms <= edge:
                h["buckets"][i] += 1
                break


def set_gauge(name: str, value: float, **labels: Any) -> None:
    key = _key(labels)
    with _lock:
        if not _guard(_gauges, name, key):
            return
        _gauges[name][key] = float(value)


def inc_gauge(name: str, n: float = 1, **labels: Any) -> None:
    key = _key(labels)
    with _lock:
        if not _guard(_gauges, name, key):
            return
        _gauges[name][key] = _gauges[name].get(key, 0.0) + n


def dec_gauge(name: str, n: float = 1, **labels: Any) -> None:
    inc_gauge(name, -n, **labels)


def _percentile(h: dict[str, Any], q: float) -> float:
    """누적 버킷 **선형보간** 근사 백분위(ms). count=0 이면 0.

    목표 순위가 걸린 버킷을 찾고, 그 버킷 [하한, 상한) 안에서 관측이 균등분포한다고 보고
    보간한다(Prometheus histogram_quantile 과 동일 가정). 버킷 상한을 그대로 반환하면
    p50 이 항상 5·10·25… 같은 눈금값으로만 나와 실제보다 크게 보고된다.
    상한이 +Inf 인 최상위 버킷은 외삽 불가 → 마지막 유한 경계로 절단(하한 편향, 정직).
    """
    count = h["count"]
    if count == 0:
        return 0.0
    target = q * count
    cum = 0
    for i, c in enumerate(h["buckets"]):
        if c == 0:
            continue
        if cum + c >= target:
            upper = BUCKETS_MS[i]
            if upper == float("inf"):
                return float(BUCKETS_MS[-2])      # 외삽 금지 — 마지막 유한 경계로 절단
            lower = float(BUCKETS_MS[i - 1]) if i > 0 else 0.0
            frac = (target - cum) / c             # 0 < frac ≤ 1
            return lower + (upper - lower) * frac
        cum += c
    return float(BUCKETS_MS[-2])


def _fmt_key(key: tuple) -> str:
    return ",".join(f"{k}={v}" for k, v in key) or "_"


def snapshot() -> dict[str, Any]:
    """현재 누적 상태의 읽기 스냅샷 — PII 없음(정수/라벨만). uptime·dropped 포함."""
    with _lock:
        counters = {n: {_fmt_key(k): v for k, v in series.items()}
                    for n, series in _counters.items()}
        gauges = {n: {_fmt_key(k): v for k, v in series.items()}
                  for n, series in _gauges.items()}
        hist = {}
        for n, series in _hist.items():
            hist[n] = {}
            for k, h in series.items():
                hist[n][_fmt_key(k)] = {
                    "count": h["count"],
                    "sum_ms": round(h["sum"], 1),
                    "avg_ms": round(h["sum"] / h["count"], 1) if h["count"] else 0.0,
                    "p50_ms": round(_percentile(h, 0.50), 1),
                    "p95_ms": round(_percentile(h, 0.95), 1),
                }
        dropped = dict(_dropped)
    return {
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "counters": counters,
        "gauges": gauges,
        "histograms": hist,
        "series_dropped": dropped,
    }


def reset() -> None:
    """테스트 전용 — 전체 초기화(uptime 제외)."""
    with _lock:
        _counters.clear()
        _gauges.clear()
        _hist.clear()
        _dropped.clear()
