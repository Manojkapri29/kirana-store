"""Internal error diagnostics: what went wrong, for the people who run the system, never for the user.

An unexpected failure gets a safe reference id (`ERR-20260921-A82F3`). The user sees only that id and a
plain message; the real exception, the endpoint, the shop and user, a category and a request correlation id
are written to the `app.diagnostics` log, one JSON object per line, so a developer or a future admin-only
screen can find the entry from the id. Nothing here is ever shown to a user.

Everything written is redacted first (`redact`): keys, tokens, passwords, database addresses, file paths,
e-mail addresses and long digit runs (phone or card numbers) are replaced, in the message and in the
traceback. The reference id is random: it contains no database id and no identifier of a person or shop.
"""

import json
import logging
import re
import secrets
import traceback
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

log = logging.getLogger("app.diagnostics")

_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O, U: easy to read out over the phone
REFERENCE_PATTERN = re.compile(r"^ERR-\d{8}-[0-9A-Z]{5}$")
CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"
    INSUFFICIENT_STOCK = "insufficient_stock"
    INVENTORY_CONFLICT = "inventory_conflict"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    PLAN_LIMIT = "plan_limit"
    NETWORK = "network"
    EXTERNAL_API = "external_api"
    TIMEOUT = "timeout"
    DATABASE = "database"
    IMAGE_UPLOAD = "image_upload"
    PROMOTION = "promotion_calculation"
    CHECKOUT = "checkout"
    UNEXPECTED = "unexpected"


def new_reference_id(now: datetime | None = None) -> str:
    """A unique, unguessable reference for one unexpected error, e.g. ERR-20260921-A82F3."""
    day = (now or datetime.now(UTC)).strftime("%Y%m%d")
    return f"ERR-{day}-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(5))


def new_correlation_id() -> str:
    return secrets.token_hex(8)


# --- Redaction -------------------------------------------------------------------------------------

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    # a value after a secret-looking name: password=..., api_key: ..., "token": "..."
    (
        re.compile(
            r"""(?ix)\b(pass(?:word|wd)?|secret|token|api[_-]?key|user[_-]?key|authorization|credential|"""
            r"""client[_-]?secret|private[_-]?key)\b(["']?\s*[:=]\s*)(["']?)[^\s,;"'})\]]+"""
        ),
        r"\1\2\3<redacted>",
    ),
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "<redacted-key>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "<redacted-key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted-key>"),
    (re.compile(r"\b(gh[pousr]|xox[baprs])[-_][A-Za-z0-9-]{10,}"), "<redacted-key>"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"]*@[^\s'\"]+"), "<address-with-credentials>"),
    (re.compile(r"\b(sqlite|postgres(?:ql)?)(?:\+\w+)?:///?[^\s'\"]+"), "<database>"),
    (re.compile(r"(?<![\w.])(?:/[\w.@+-]+){2,}/?"), "<path>"),  # /Users/x/app/file.py
    (re.compile(r"\b[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]*"), "<path>"),  # C:\dir\file
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?<![\w.])\+?\d[\d -]{8,}\d(?![\w])"), "<number>"),
]


def redact(text: str) -> str:
    """Remove anything that looks like a secret, a credential, a path, an address or a personal number."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def sanitized_summary(exc: BaseException, limit: int = 300) -> str:
    """The exception type and a redacted, shortened message. Safe to log; still never shown to users."""
    message = redact(" ".join(str(exc).split()))
    return f"{type(exc).__name__}: {message}"[:limit]


class _RedactingFilter(logging.Filter):
    """Redacts anything else written to the diagnostics logger. Entries from `record_error` have their
    free-text fields redacted one by one, so structural fields (the endpoint) stay readable."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "pre_redacted", False):
            record.msg = redact(record.getMessage())
            record.args = None
        return True


log.addFilter(_RedactingFilter())


def configure(log_file: str | None) -> None:
    """Optionally also write diagnostics to a file (JSON lines). Called once when the app is created."""
    if log_file and not any(isinstance(h, logging.FileHandler) for h in log.handlers):
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)


def record_error(
    *,
    reference_id: str | None,
    category: ErrorCategory,
    error_code: str,
    status: int,
    endpoint: str,
    method: str,
    correlation_id: str | None,
    shop_id: int | None,
    user_id: int | None,
    exc: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write one diagnostics entry. The traceback goes to the internal log only."""
    entry = {
        "event": "error",
        "reference_id": reference_id,
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "endpoint": endpoint,
        "method": method,
        "status": status,
        "shop_id": shop_id,
        "user_id": user_id,
        "category": category.value,
        "error_code": error_code,
        "exception_type": type(exc).__name__ if exc is not None else None,
        "details": sanitized_summary(exc) if exc is not None else None,
        "correlation_id": correlation_id,
        **(extra or {}),
    }
    if exc is not None and status >= 500:  # the traceback stays inside the single-line entry, redacted
        entry["traceback"] = redact("".join(traceback.format_exception(exc)))[-6000:]
    level = logging.ERROR if status >= 500 else logging.WARNING
    log.log(level, json.dumps(entry, default=str), extra={"pre_redacted": True})
