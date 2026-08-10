"""HTTPS 검증 컨텍스트 한 곳.

macOS 의 python.org 빌드는 시스템 키체인의 루트 인증서를 쓰지 않는다. 그래서
`ssl.create_default_context()` 가 만든 컨텍스트로 `huggingface.co` 를 치면
`CERTIFICATE_VERIFY_FAILED` 가 난다 — 코드가 틀려서가 아니라 신뢰 저장소가 비어서다.

이 저장소에서 이미 **네 자리**가 같은 이유로 깨졌거나 깨질 수 있었다.

    garim/proxy.py            상류 LLM 호출        ← 제품 경로
    garim/training/models.py  모델 라이선스 게이트
    garim/training/sources.py 원천 라이선스 게이트
    garim/training/assets.py  KDPII 내려받기

네 곳이 각자 컨텍스트를 만들면 다음 사람이 다섯 번째를 또 놓친다. 그래서 하나로 모은다.

## 검증을 끄는 선택지는 두지 않는다

`verify_mode = CERT_NONE` 은 이 오류를 즉시 없애지만, 그 순간 프록시가 상류로 보내는
프롬프트와 게이트가 받아오는 라이선스가 중간자에게 열린다. 프록시는 사용자의
`Authorization` 헤더를 그대로 전달하므로(§8.1-17) 평문 강등은 곧 키 유출이다.
**끄는 대신 인증서 번들을 명시적으로 들고 간다.**

## certifi 가 없으면

기본 컨텍스트로 돌아간다. Linux CI 처럼 시스템 신뢰 저장소가 제대로 있는 환경에서는
그것이 옳고, certifi 를 필수 의존성으로 만들면 검사기 본체가 쓰지도 않는 패키지를
끌고 오게 된다. 검사기는 소켓을 하나도 열지 않는다(`tools/no_external_calls.py`).
"""

from __future__ import annotations

import ssl


def https_context() -> ssl.SSLContext:
    """검증이 켜진 TLS 컨텍스트. certifi 가 있으면 그 번들을 쓴다."""
    try:
        import certifi
    except ModuleNotFoundError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())
