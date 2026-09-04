from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from northstar_api.config import Settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SECRET_PATTERNS = (
    re.compile(r"nvapi-[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)(password|secret|api[_-]?key)(\s*[=:]\s*)[^\s,;]+"),
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(token in key.lower() for token in ("password", "secret", "token", "api_key"))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", result)
    return result


def _redact_event(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    return cast(EventDict, redact(event_dict))


def _add_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("request_id", request_id_ctx.get())
    return event_dict


def configure_logging(settings: Settings) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_event,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = structlog.processors.JSONRenderer() if settings.log_json else structlog.dev.ConsoleRenderer()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level.upper(), force=True)
    # httpx logs complete request URLs at INFO. OAuth endpoints place short-lived
    # codes and (per Meta's documented exchange) client secrets in query
    # parameters, so provider HTTP client URLs must never reach application logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
