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


def test_percentile_interpolates_inside_bucket():
    """백분위는 버킷 상한을 그대로 뱉지 않고 [하한,상한) 안에서 보간해야 한다(과대보고 방지)."""
    M.reset()
    for _ in range(100):
        M.observe("lat_ms", 30.0)          # 전량 (25,50] 버킷
    h = M.snapshot()["histograms"]["lat_ms"]["_"]
    assert 25.0 < h["p50_ms"] < 50.0, h    # 버킷 상한(50)으로 붙지 않음
    assert h["p50_ms"] <= h["p95_ms"] <= 50.0


def test_percentile_empty_and_monotonic():
    M.reset()
    assert M._percentile({"buckets": [0] * len(M.BUCKETS_MS), "sum": 0.0, "count": 0}, 0.5) == 0.0
    for v in (5, 50, 500, 5000):
        M.observe("lat2_ms", v)
    h = M.snapshot()["histograms"]["lat2_ms"]["_"]
    assert h["p50_ms"] <= h["p95_ms"]


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


def test_monitor_leak_alarm_reflects_counter():
    """★모니터의 leak 경보가 실제 카운터에 물려 있는지★ — 0=초록 문구, ≥1=경보 문구.

    (회귀 이력: monitor 가 코드베이스 어디서도 방출하지 않는 leaks_total 을 읽어 경보가
     구조적으로 절대 뜨지 않았다. 이 테스트가 그 재발을 막는다.)"""
    from scripts.monitor import _render
    base = {"build": {}, "in_flight": {}, "ready": True, "uptime_s": 1, "histograms": {}}
    clean = _render({**base, "counters": {}}, None, "u")
    assert "유출 0" in clean and "LEAK 경보" not in clean
    dirty = _render({**base, "counters": {"leaks_total": {"tool=send_email,rule=egress.email_domains": 2}}},
                    None, "u")
    assert "LEAK 경보" in dirty and "2건" in dirty


def test_leaks_total_emitted_only_when_gate_absent():
    """★critical 불변식 계측★ — 무방어 arm 은 실제 유출 1건, Tier A arm 은 0건이어야 한다.

    leaks_total 은 '게이트가 무엇을 막았나'가 아니라 '실제로 allowlist 밖으로 무엇이 나갔나'를
    센다. 게이트가 켜진 채 이 값이 오르면 게이트 회귀다."""
    import asyncio
    from scripts.run_demo import _run_arm

    async def _leaks(defs: dict) -> dict:
        M.reset()
        await _run_arm(defs, "poisoned_kb", arm="t")
        return M.snapshot()["counters"].get("leaks_total", {})

    none_leaks = asyncio.run(_leaks({}))
    assert sum(none_leaks.values()) == 1, none_leaks
    assert any("send_email" in k and "egress.email_domains" in k for k in none_leaks)
    assert asyncio.run(_leaks({"tier_a": True})) == {}


def test_demo_episode_ids_are_deterministic():
    """같은 (시나리오, arm) 은 항상 같은 episode_id — 커밋된 트레이스가 바이트 재현된다."""
    from scripts.run_demo import _demo_episode_id
    a = _demo_episode_id("poisoned_kb", "tier_a")
    assert a == _demo_episode_id("poisoned_kb", "tier_a")
    assert a != _demo_episode_id("poisoned_kb", "none")
    assert len(a) == 12
