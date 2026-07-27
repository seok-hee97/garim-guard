"""Provider 복원력 — 일시 실패(429/5xx/타임아웃/연결)만 지수백오프+지터로 재시도.

SDK별 예외 타입 import 에 의존하지 않고 status code·예외명 휴리스틱으로 판정한다
(openai / google-genai 어느 쪽이든 동작). 영구 실패(400/401)는 즉시 실패.
"""

from __future__ import annotations

import tenacity

_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_TRANSIENT_NAME_HINTS = (
    "timeout", "connection", "ratelimit", "internalserver",
    "serviceunavailable", "overloaded", "apiconnection", "temporar",
)


def _is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        if int(status) in _TRANSIENT_STATUS:
            return True
    except (TypeError, ValueError):
        pass
    name = type(exc).__name__.lower()
    return any(h in name for h in _TRANSIENT_NAME_HINTS)


def transient_retry(*, attempts: int = 5, max_wait: float = 30.0):
    """async/sync 함수 모두에 쓸 수 있는 재시도 데코레이터.

    일시 실패만 재시도(최대 attempts 회), 소진 시 마지막 예외를 그대로 raise(reraise).
    """
    return tenacity.retry(
        retry=tenacity.retry_if_exception(_is_transient),
        wait=tenacity.wait_exponential_jitter(initial=1, max=max_wait),
        stop=tenacity.stop_after_attempt(attempts),
        reraise=True,
    )
