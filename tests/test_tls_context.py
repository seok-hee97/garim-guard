"""HTTPS 검증 컨텍스트가 **모든** 나가는 호출에 붙어 있는지."""

from __future__ import annotations

import re
import ssl
from pathlib import Path

import pytest

from garim.tls import https_context

# 이 저장소에서 밖으로 나가는 자리 전부. 새 자리가 생기면 여기 추가해야 하고,
# 추가를 잊으면 아래 `test_no_unlisted_call_site` 가 잡는다.
OUTBOUND = (
    "garim/proxy.py",  # 상류 LLM — 제품 경로
    "garim/training/models.py",  # 모델 라이선스 게이트
    "garim/training/sources.py",  # 원천 라이선스 게이트
    "garim/training/assets.py",  # KDPII 내려받기
    "bench/mask_quality.py",  # §8.1-13b 하네스
)
_URLOPEN = re.compile(r"urlopen\(")
_WITH_CONTEXT = re.compile(r"urlopen\((?:[^()]|\([^()]*\))*context=https_context\(\)", re.S)


def test_context_verifies_by_default() -> None:
    """검증을 끄면 프록시가 상류로 보내는 프롬프트와 사용자 키가 중간자에게 열린다."""
    context = https_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_context_carries_a_nonempty_trust_store() -> None:
    """macOS 의 python.org 빌드는 시스템 키체인을 안 쓴다 — 비어 있으면 전부 실패한다."""
    assert https_context().get_ca_certs(), "신뢰 저장소가 비었다 — certifi 를 설치해야 한다"


@pytest.mark.parametrize("path", OUTBOUND)
def test_every_outbound_call_uses_the_shared_context(path: str) -> None:
    """★ 이 테스트가 잡는 회귀가 자산 다운로드를 통째로 세웠다.

    네 자리가 각자 `urlopen` 을 부르는데 그중 셋에 컨텍스트가 없어, macOS 에서
    `CERTIFICATE_VERIFY_FAILED` 로 라이선스 게이트가 열리지 않았다. 한 곳을 고쳐도
    다음 사람이 다섯 번째를 또 놓치므로 자리 목록째 고정한다.
    """
    source = Path(path).read_text(encoding="utf-8")
    calls = len(_URLOPEN.findall(source))
    guarded = len(_WITH_CONTEXT.findall(source))
    assert calls and calls == guarded, f"{path}: urlopen {calls}회 중 {guarded}회만 검증 컨텍스트"


def test_no_unlisted_call_site() -> None:
    """목록에 없는 곳에서 밖으로 나가면 위 검사가 통째로 비켜간다."""
    found = {
        str(path)
        for directory in ("garim", "bench", "train", "tools")
        for path in Path(directory).rglob("*.py")
        if _URLOPEN.search(path.read_text(encoding="utf-8"))
    }
    assert found == set(OUTBOUND), f"목록 밖 호출 자리: {sorted(found - set(OUTBOUND))}"
