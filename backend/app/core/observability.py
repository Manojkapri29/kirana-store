"""Logging and request correlation: one request id per request, structured log lines, and named log categories.

Every request carries an id (`req_...`), reused from a well-formed `X-Request-ID` or generated. It is kept in a context
variable so anything running for that request (a service writing an audit row, an integration failure, a log line) can
attach it without being passed it. Log lines are one JSON object (or plain text, by setting); secrets, tokens, paths,
e-mail addresses and long digit runs are removed by the same redaction that protects error diagnostics. Query strings are
never logged, and neither are request bodies, uploaded files or headers.

Categories are separate loggers, so an operator can route or silence them independently:
`app.access` (requests), `app.security`, `app.audit`, `app.integration`, `app.ai`, `app.backup`, `app.notification`.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core import diagnostics

CATEGORIES = ("access", "security", "audit", "integration", "ai", "backup", "notification", "application")
_PLAIN_FIELDS = {"endpoint", "method", "request_id", "category", "source", "code"}
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, category, message, plus the fields the caller gave."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "category": getattr(record, "category", record.name.removeprefix("app.")),
            "message": diagnostics.redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or current_request_id(),
        }
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str)


def configure_logging(level: str, fmt: str) -> None:
    """Set the root level and, for `json`, a JSON formatter on the application's own handlers (idempotent)."""
    logging.basicConfig(level=level)
    root = logging.getLogger()
    root.setLevel(level)
    if fmt == "json":
        for handler in root.handlers:
            if not isinstance(handler.formatter, JsonFormatter):
                handler.setFormatter(JsonFormatter())
    for name in CATEGORIES:
        logging.getLogger(f"app.{name}").setLevel(level)


def log_event(category: str, message: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Write one categorised log line. Values are redacted; `password`, `token`, `key` and `secret` fields are dropped."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():  # `endpoint` is already a masked route, so it is kept as written
        if any(word in key.lower() for word in ("password", "token", "secret", "key", "authorization")):
            continue
        clean[key] = (
            diagnostics.redact(value) if isinstance(value, str) and key not in _PLAIN_FIELDS else value
        )
    logging.getLogger(f"app.{category}").log(
        level, message, extra={"category": category, "fields": clean, "request_id": current_request_id()}
    )
