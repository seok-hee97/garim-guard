"""onnxruntime intra-op 스레드 — 컨테이너 CPU 할당량을 넘겨 throttling 되지 않게."""

from __future__ import annotations

from pathlib import Path

import pytest

from garim.detect import l1
from garim.detect.l1 import cpu_quota_threads


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "cpu_max": tmp_path / "cpu.max",
        "v1_quota": tmp_path / "cfs_quota_us",
        "v1_period": tmp_path / "cfs_period_us",
    }


def test_no_cgroup_files_means_onnxruntime_default(tmp_path: Path) -> None:
    assert cpu_quota_threads(**_paths(tmp_path)) is None


def test_cgroup_v1_quota_floors_to_whole_cores(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["v1_quota"].write_text("850000\n")  # RunPod 실측 — 8.5코어
    paths["v1_period"].write_text("100000\n")
    assert cpu_quota_threads(**paths) == 8
    paths["v1_quota"].write_text("-1\n")
    assert cpu_quota_threads(**paths) is None


def test_cgroup_v2_quota_and_unlimited(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["cpu_max"].write_text("50000 100000\n")
    assert cpu_quota_threads(**paths) == 1
    paths["cpu_max"].write_text("max 100000\n")
    assert cpu_quota_threads(**paths) is None


def test_environment_overrides_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARIM_L1_THREADS", "3")
    assert l1._intra_op_threads() == 3
    monkeypatch.setenv("GARIM_L1_THREADS", "0")
    with pytest.raises(ValueError):
        l1._intra_op_threads()
