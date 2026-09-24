"""Per-shop finance configuration: approval limits and alert sensitivities.

An approval limit left as None is OFF; there is no built-in number. Every change is audited with before/after.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import FinanceSettings
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError

MONEY_FIELDS = (
    "expense_approval_threshold", "adjustment_approval_threshold", "cash_adjustment_threshold",
    "cash_variance_alert_amount",
)  # fmt: skip
INT_FIELDS = ("overdue_after_days", "expense_spike_pct", "margin_drop_points")
BOOL_FIELDS = ("period_reopen_requires_approval",)
FIELDS = MONEY_FIELDS + INT_FIELDS + BOOL_FIELDS


def get_settings(session: Session, shop_id: int) -> FinanceSettings:
    """The shop's settings row, created with its defaults the first time it is needed."""
    row = session.scalar(select(FinanceSettings).where(FinanceSettings.shop_id == shop_id))
    if row is None:
        row = FinanceSettings(shop_id=shop_id)
        session.add(row)
        session.flush()
        session.refresh(row)
    return row


def as_dict(row: FinanceSettings) -> dict[str, Any]:
    return {name: getattr(row, name) for name in FIELDS}


def update_settings(session: Session, ctx: RequestContext, changes: dict[str, Any]) -> FinanceSettings:
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise InvalidInputError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    row = get_settings(session, ctx.shop_id)
    before = as_dict(row)
    for name, value in changes.items():
        if name in MONEY_FIELDS and value is not None:
            if isinstance(value, float) or not isinstance(value, Decimal | int) or Decimal(value) < 0:
                raise InvalidInputError("Enter a non-negative amount.", field=name)
        if name in INT_FIELDS:
            minimum = 1 if name == "overdue_after_days" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise InvalidInputError("Enter a whole number that is not negative.", field=name)
        if name == "cash_variance_alert_amount" and value is None:
            raise InvalidInputError("This setting cannot be blank.", field=name)
        setattr(row, name, value)
    session.flush()
    record_audit(
        session, ctx, entity_type="finance_settings", entity_id=row.id, action="finance_settings_changed",
        before=before, after=as_dict(row),
    )  # fmt: skip
    return row
