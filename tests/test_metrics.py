"""모니터링 구성요소 회귀 고정 — 메트릭 레지스트리 · /v1/metrics · 미들웨어 · 진행모니터.

'프로그램 실행 시 무엇이 실행/진행 중인지' 관측 기능이 (1) PII 없이 집계되고, (2) HTTP 로
노출되며, (3) 긴 작업 진행을 heartbeat 로 남기는지 고정한다. 이 테스트가 깨지면 모니터링이 회귀.
"""

from __future__ import annotations

import json

from src.agent import metrics as M


def test_counter_gauge_histogram_snapshot():
    M.reset()
    M.inc("gate_blocks_total", rule="egress.email_domains")
    M.inc("gate_blocks_total", rule="egress.email_domains")
    M.inc("gate_blocks_total", rule="dlp.egress")
    M.inc_gauge("episodes_in_flight"); M.inc_gauge("episodes_in_flight"); M.dec_gauge("episodes_in_flight")
    for v in (5, 50, 500, 5000):
        M.observe("episode_latency_ms", v)
    snap = M.snapshot()
    assert snap["counters"]["gate_blocks_total"]["rule=egress.email_domains"] == 2
    assert snap["counters"]["gate_blocks_total"]["rule=dlp.egress"] == 1
    assert snap["gauges"]["episodes_in_flight"]["_"] == 1.0
    h = snap["histograms"]["episode_latency_ms"]["_"]
    assert h["count"] == 4 and h["p50_ms"] > 0 and h["p95_ms"] >= h["p50_ms"]


def test_cardinality_guard_drops_excess_series():
    """라벨 폭발/PII 오염 fail-safe — metric 당 시리즈 상한 초과분은 드롭 + dropped 카운트."""
    M.reset()
    for i in range(M._MAX_SERIES + 20):
        M.inc("bogus_total", label=str(i))
    snap = M.snapshot()
    assert len(snap["counters"]["bogus_total"]) == M._MAX_SERIES
    assert snap["series_dropped"]["bogus_total"] == 20


def test_snapshot_has_no_pii_by_construction():
    """레지스트리는 정수/라벨만 — 원본 값 저장 자료구조가 없음을 스냅샷 직렬화로 확인."""
    M.reset()
    M.inc("dlp_redactions_total", entity="KR_RRN")     # 타입만, 값 아님
    blob = json.dumps(M.snapshot(), ensure_ascii=False)
    assert "900101" not in blob and "@" not in blob
    assert "KR_RRN" in blob


def test_metrics_endpoint_and_request_middleware():
    """GET /v1/metrics 가 스냅샷을 반환하고, 미들웨어가 모든 요청을 http_requests_total 로 계측."""
    import os
    os.environ["APP_ENV"] = "test"
    from fastapi.testclient import TestClient
    from src.agent.server import app
    M.reset()
    with TestClient(app) as c:
        c.get("/healthz"); c.get("/healthz")
        r = c.get("/v1/metrics")
        assert r.status_code == 200
        j = r.json()
        assert "uptime_s" in j and "in_flight" in j and "counters" in j
        assert j["counters"]["http_requests_total"]["method=GET,route=/healthz,status=200"] >= 2
        assert r.headers.get("X-Request-ID")           # 상관 ID 에코


def test_middleware_uses_route_template_not_concrete_path():
    """경로 파라미터(식별자) 유출 방지 — 라벨은 라우트 템플릿이어야(concrete path 아님)."""
    import os
    os.environ["APP_ENV"] = "test"
    from fastapi.testclient import TestClient
    from src.agent.server import app
    M.reset()
    with TestClient(app) as c:
        c.get("/v1/approvals/appr_SECRET123", headers={"X-API-Key": "demo-approver-key"})
        snap = M.snapshot()
        keys = " ".join(snap["counters"].get("http_requests_total", {}))
        assert "appr_SECRET123" not in keys              # 식별자가 라벨에 새지 않음
        assert "{approval_id}" in keys or "unmatched" in keys


def test_progress_monitor_heartbeat(tmp_path):
    from eval.progress import ProgressMonitor
    hb = tmp_path / "progress.json"
    pm = ProgressMonitor(total=3, phase="test", out_path=hb, flush_every_s=0.0)
    pm.tick("case1", executed=1)
    pm.tick("case2", blocked=1)
    pm.done()
    snap = json.loads(hb.read_text("utf-8"))
    assert snap["total"] == 3 and snap["done"] == 2
    assert snap["counts"] == {"executed": 1, "blocked": 1}
    assert "eta_s" in snap and "rate_per_s" in snap and "ts" in snap


def test_monitor_renders_server_down_gracefully():
    """서버 미가동 시 monitor 가 크래시 없이 'server down' 을 표시."""
    from scripts.monitor import _render
    out = _render(None, None, "http://localhost:8000/v1/metrics")
    assert "server down" in out
