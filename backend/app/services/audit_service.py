"""Writes the audit log: who changed what, with before/after values. The log is insert-only."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import AuditLog


def to_jsonable(value: Any) -> Any:
    """Make a value safe for a JSON column. Money and quantities become strings, so no precision is lost."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    return value


def record_audit(
    session: Session,
    ctx: RequestContext,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            shop_id=ctx.shop_id,
            user_id=ctx.user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_json=to_jsonable(before),
            after_json=to_jsonable(after),
        )
    )
