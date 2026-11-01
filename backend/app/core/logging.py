"""Logging with credential redaction.

Requirement 12.15 of the brief: API keys must never appear in logs. Every log
record and every structured `details` blob written to `agent_activity_logs`
passes through `redact()` first.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

from app.core.config import settings

# Keys whose values must never be rendered, whatever their nesting depth.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|x[_-]api[_-]key|secret|password|token|bearer|cdp_url)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


def _build_value_patterns() -> list[re.Pattern[str]]:
    """Literal patterns for the actual configured secrets.

    Catches the case where a key is interpolated into a URL or an error string
    by a third-party library rather than passed as a named field.
    """
    patterns = []
    for secret in (settings.tinyfish_api_key, settings.gemini_api_key, settings.jwt_secret):
        if secret and len(secret) >= 8:
            patterns.append(re.compile(re.escape(secret)))
    return patterns


_VALUE_PATTERNS = _build_value_patterns()


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively strip secrets from arbitrary structured data."""
    if _depth > 12:
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SENSITIVE_KEY_PATTERN.search(k):
                out[k] = _REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        for pattern in _VALUE_PATTERNS:
            value = pattern.sub(_REDACTED, value)
        return value
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never let logging break the request
            pass
        return True


def configure_logging() -> None:
    level = logging.DEBUG if settings.debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These libraries log full request URLs, which can carry query credentials.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
