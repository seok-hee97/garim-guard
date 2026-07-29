"""구조적 JSON 로깅 — 요청 단위 상관(episode_id) + PII 미로깅.

운영 관측의 기반. `print` 대신 이 로거를 쓴다. ★PII(원본 값)는 절대 로깅하지 않는다★
(policy.yaml `log_mappings: false` 준수) — 엔티티 타입·카운트·규칙 키·결정만 남긴다.
호출 측에서 값 대신 타입/카운트를 넘기는 규율을 지켜야 한다.

    from . import log as alog
    alog.configure()                       # 앱 기동 시 1회
    alog.bind(episode_id="abc123")         # 요청 스코프
    log = alog.get_logger()
    alog.event(log, "info", "gate", tool="send_email", decision="BLOCK", rule="egress.email_domains")
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys

_episode_id: contextvars.ContextVar[str] = contextvars.ContextVar("episode_id", default="-")
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_LOGGER_NAME = "safeagent"
# CLI/라이브러리 호출자가 configure()를 부르지 않은 경우 Python의 lastResort 핸들러가
# ERROR 메시지 본문만("leak_detected") 터미널에 흘리는 것을 막는다. 서비스는 configure()
# 호출 시 아래 NullHandler를 실제 JSON 핸들러로 교체한다.
logging.getLogger(_LOGGER_NAME).addHandler(logging.NullHandler())


def bind(episode_id: str | None = None, request_id: str | None = None) -> None:
    """요청 스코프 상관 ID 를 바인딩한다(contextvar — async 태스크별 격리)."""
    if episode_id is not None:
        _episode_id.set(episode_id)
    if request_id is not None:
        _request_id.set(request_id)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "episode_id": _episode_id.get(),
            "request_id": _request_id.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            base.update(fields)
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info).splitlines()[-1]  # 스택 전체 대신 요약
        return json.dumps(base, ensure_ascii=False)


def configure(level: str = "INFO") -> logging.Logger:
    """앱 기동 시 1회 호출. stdout JSON 핸들러를 붙인다(멱등)."""
    logger = logging.getLogger(_LOGGER_NAME)
    real_handlers = [h for h in logger.handlers if not isinstance(h, logging.NullHandler)]
    if not real_handlers:
        logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.NullHandler)]
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_JsonFormatter())
        logger.addHandler(h)
        logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def event(logger: logging.Logger, level: str, msg: str, **fields) -> None:
    """구조적 이벤트. ★fields 에 원본 PII 값을 넣지 말 것★ — 타입/카운트/규칙만."""
    logger.log(getattr(logging, level.upper(), logging.INFO), msg, extra={"fields": fields})
