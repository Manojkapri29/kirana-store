"""System events: short, safe facts about how the platform is behaving, for operators (never for business analysis).

"An outside price service was unavailable", "a notification could not be delivered", "the backup failed". They hold a
category, a severity, a source, a fixed code and one safe sentence: never a customer, an amount, a document, a token or a
raw exception. Recording never fails a request: it runs in its own savepoint and any problem is logged and dropped.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import diagnostics, observability
from app.db.types import utc_now
from app.models import SystemEvent
from app.models.enums import EventSeverity

CATEGORIES = ("integration", "ai", "notification", "backup", "security", "application")


def record(
    session: Session,
    *,
    category: str,
    severity: EventSeverity,
    source: str,
    code: str,
    message: str,
    shop_id: int | None = None,
) -> None:
    """Best effort: a failure to record is logged and ignored, and never disturbs the caller's transaction."""
    try:
        with session.begin_nested():
            session.add(
                SystemEvent(
                    shop_id=shop_id,
                    category=category,
                    severity=severity,
                    source=source[:60],
                    code=code[:60],
                    message=diagnostics.redact(message)[:300],
                    request_id=observability.current_request_id(),
                )
            )
    except Exception:  # noqa: BLE001
        observability.log_event(
            "application", "could not record a system event", level=logging.WARNING, source=source
        )
        return
    level = logging.ERROR if severity is EventSeverity.ERROR else logging.WARNING
    observability.log_event(category, message, level=level, source=source, code=code, shop_id=shop_id)


def recent(
    session: Session,
    *,
    category: str | None = None,
    shop_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SystemEvent], int]:
    conditions = []
    if category:
        conditions.append(SystemEvent.category == category)
    if shop_id is not None:
        conditions.append(SystemEvent.shop_id == shop_id)
    total = session.scalar(select(func.count()).select_from(SystemEvent).where(*conditions)) or 0
    rows = session.scalars(
        select(SystemEvent).where(*conditions).order_by(SystemEvent.id.desc()).limit(limit).offset(offset)
    )
    return list(rows), total


def counts_since(session: Session, hours: int = 24, shop_id: int | None = None) -> dict[str, dict[str, int]]:
    """Events per category and severity over the last `hours`: the operator's at-a-glance view."""
    since: datetime = utc_now() - timedelta(hours=hours)
    query = (
        select(SystemEvent.category, SystemEvent.severity, func.count())
        .where(SystemEvent.created_at >= since)
        .group_by(SystemEvent.category, SystemEvent.severity)
    )
    if shop_id is not None:
        query = query.where(SystemEvent.shop_id == shop_id)
    out: dict[str, dict[str, int]] = {}
    for category, severity, count in session.execute(query):
        out.setdefault(category, {})[severity.value] = count
    return out
