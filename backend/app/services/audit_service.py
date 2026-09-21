"""Writes the audit log: who changed what, with before/after values. The log is insert-only."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import observability
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
            request_id=observability.current_request_id(),
        )
    )


def list_entries(
    session: Session, shop_id: int, *, entity_type: str | None, limit: int, offset: int
) -> tuple[list[AuditLog], int]:
    """One shop's audit entries, newest first. Only that shop's rows are ever returned."""
    conditions = [AuditLog.shop_id == shop_id]
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    total = session.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = session.scalars(
        select(AuditLog).where(*conditions).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return list(rows), total


def record_system_audit(
    session: Session,
    shop_id: int,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """An audit entry for something the PLATFORM did to a shop (a state or plan change): no shop user."""
    session.add(
        AuditLog(
            shop_id=shop_id,
            user_id=None,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_json=to_jsonable(before),
            after_json=to_jsonable(after),
            request_id=observability.current_request_id(),
        )
    )
