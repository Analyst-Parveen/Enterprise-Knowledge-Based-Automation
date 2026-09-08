"""Structured JSON logging with correlation IDs and secret redaction.

Never log: JWTs, credentials, PII, or full document contents.
See .claude/rules/security.md section 7.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.core.config import settings
from app.core.context import get_correlation_id, get_request_context

# Patterns redacted from every log record before it is written.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"), "<jwt-redacted>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<aws-key-redacted>"),
    (
        re.compile(r"(?i)(authorization|api[_-]?key|password|secret|token)[\"':=\s]+\S+"),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"-----BEGIN[^-]+PRIVATE KEY-----[\s\S]*?-----END[^-]+-----"),
        "<private-key-redacted>",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    """One JSON object per line, always carrying the correlation ID."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": get_correlation_id() or None,
        }

        ctx = get_request_context()
        if ctx is not None:
            payload["tenant_id"] = ctx.tenant_id
            payload["user_id"] = ctx.user_id

        # structured extras attached via logger.info("msg", extra={"extra": {...}})
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if k not in payload})

        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # uvicorn duplicates access logs; route them through our formatter instead
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_security_event(
    event: str,
    *,
    reason: str,
    severity: str = "warning",
    **fields: Any,
) -> None:
    """Security events are logged with a reason code, never the offending payload.

    These are also persisted to audit_events by the caller where a DB session
    is available. See .claude/rules/security.md section 7.
    """
    logger = get_logger("security")
    ctx = get_request_context()
    payload = {
        "event_type": event,
        "reason": reason,
        "severity": severity,
        "tenant_id": ctx.tenant_id if ctx else None,
        "user_id": ctx.user_id if ctx else None,
        **fields,
    }
    level = logging.ERROR if severity in ("critical", "error") else logging.WARNING
    logger.log(level, "security_event:%s", event, extra={"extra": payload})
