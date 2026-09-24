"""Neutral, factual finance alerts. Every alert states a measured fact and a recommendation to look; none
accuses anyone of anything ("fraud", "theft", "suspicious" never appear: there is no verified fraud system).
Wording is limited to phrases such as "Unusual variance detected", "Review recommended" and "Missing cost data".

Thresholds come from the shop's finance settings (`finance_settings_service`), never from constants here:
  * `expense_spike_pct`      - % rise that counts as a variance (used for expenses and for supplier payables)
  * `overdue_after_days`     - a customer balance older than this many days (from the charge's own date, since
                               no due dates exist) is reported as aged
  * `margin_drop_points`     - fall in gross margin, in percentage points
  * `cash_variance_alert_amount` - a counted-vs-expected cash difference above this amount
Alerts are computed on demand from the books, so they always agree with the reports.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashCount, SystemEvent
from app.services import (
    expense_service,
    finance_period_service,
    payables_service,
    pnl_service,
    receivables_service,
    reconciliation_service,
)
from app.services.finance_settings_service import get_settings

ZERO = Decimal("0.00")
WINDOW_DAYS = 30


@dataclass(frozen=True)
class FinanceAlert:
    code: str
    severity: str  # INFO or REVIEW
    title: str
    message: str
    figures: dict[str, str] = field(default_factory=dict)


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return ((current - previous) / previous * 100).quantize(Decimal("0.1"))


def compute(session: Session, shop_id: int, today: date) -> list[FinanceAlert]:
    s = get_settings(session, shop_id)
    alerts: list[FinanceAlert] = []
    cur_start, prev_end = today - timedelta(days=WINDOW_DAYS - 1), today - timedelta(days=WINDOW_DAYS)
    prev_start = prev_end - timedelta(days=WINDOW_DAYS - 1)

    # 1. Expenses far above the previous window
    cur_exp = expense_service.posted_expense_total(session, shop_id, cur_start, today)
    prev_exp = expense_service.posted_expense_total(session, shop_id, prev_start, prev_end)
    change = _pct_change(cur_exp, prev_exp)
    if change is not None and change >= s.expense_spike_pct:
        alerts.append(FinanceAlert(
            "EXPENSE_VARIANCE", "REVIEW", "Unusual variance detected in expenses",
            f"Posted expenses for the last {WINDOW_DAYS} days are {cur_exp}, {change}% above the previous {WINDOW_DAYS} days ({prev_exp}). Review recommended.",
            {"current": str(cur_exp), "previous": str(prev_exp), "change_pct": str(change)},
        ))  # fmt: skip

    # 2. Customer balances aged beyond the shop's own threshold
    rec = receivables_service.compute(session, shop_id, today)
    aged = [
        c
        for c in rec.customers
        if c.oldest_open_days is not None and c.oldest_open_days > s.overdue_after_days
    ]
    if aged:
        total = sum((c.balance for c in aged), ZERO)
        alerts.append(FinanceAlert(
            "AGED_CUSTOMER_BALANCE", "REVIEW", "Customer balances aged beyond your limit",
            f"{len(aged)} customer(s) owe {total} on charges older than {s.overdue_after_days} days (measured from the transaction date; no due dates are recorded). Review recommended.",
            {"customers": str(len(aged)), "amount": str(total), "days": str(s.overdue_after_days)},
        ))  # fmt: skip

    # 3. Supplier payables rising
    pay_now = payables_service.compute(session, shop_id, today).total_payable
    pay_before = payables_service.compute(session, shop_id, today - timedelta(days=WINDOW_DAYS)).total_payable
    change = _pct_change(pay_now, pay_before)
    if change is not None and change >= s.expense_spike_pct:
        alerts.append(FinanceAlert(
            "PAYABLE_INCREASING", "REVIEW", "Supplier payable is increasing",
            f"What you owe suppliers is {pay_now}, {change}% more than {WINDOW_DAYS} days ago ({pay_before}). Review recommended.",
            {"now": str(pay_now), "before": str(pay_before), "change_pct": str(change)},
        ))  # fmt: skip

    # 4. Cash count differences
    for c in session.scalars(
        select(CashCount).where(CashCount.shop_id == shop_id, CashCount.count_date >= cur_start, CashCount.difference.is_not(None))
        .order_by(CashCount.count_date.desc(), CashCount.id.desc())
    ):  # fmt: skip
        if abs(c.difference) > s.cash_variance_alert_amount:
            alerts.append(FinanceAlert(
                "CASH_COUNT_VARIANCE", "REVIEW", "Cash count variance",
                f"On {c.count_date} the counted cash was {c.actual_cash} against an expected {c.expected_cash} (difference {c.difference}). Review recommended.",
                {"date": c.count_date.isoformat(), "difference": str(c.difference)},
            ))  # fmt: skip

    # 5 and 6. Margin and missing cost
    cur = pnl_service.compute(session, shop_id, cur_start, today)
    prev = pnl_service.compute(session, shop_id, prev_start, prev_end)
    if cur.gross_margin_pct is not None and prev.gross_margin_pct is not None:
        drop = prev.gross_margin_pct - cur.gross_margin_pct
        if drop >= s.margin_drop_points:
            alerts.append(FinanceAlert(
                "FALLING_MARGIN", "REVIEW", "Gross margin is falling",
                f"Gross margin is {cur.gross_margin_pct}% for the last {WINDOW_DAYS} days, down from {prev.gross_margin_pct}%. Review recommended.",
                {"current_pct": str(cur.gross_margin_pct), "previous_pct": str(prev.gross_margin_pct)},
            ))  # fmt: skip
    if cur.detailed_sales_without_cost or cur.returns_without_cost:
        alerts.append(FinanceAlert(
            "MISSING_COST", "INFO", "Missing cost data",
            f"{cur.detailed_sales_without_cost} sale(s) and {cur.returns_without_cost} return(s) in the last {WINDOW_DAYS} days have an unknown cost, so profit cannot be fully stated.",
            {"sales": str(cur.detailed_sales_without_cost), "returns": str(cur.returns_without_cost)},
        ))  # fmt: skip

    # 7. Payments nobody has confirmed
    count, amount = reconciliation_service.unreconciled_count(session, shop_id, cur_start, today)
    if count:
        alerts.append(FinanceAlert(
            "UNRECONCILED_PAYMENTS", "INFO", "Payments not yet reconciled",
            f"{count} electronic payment(s) totalling {amount} in the last {WINDOW_DAYS} days are not marked as matched.",
            {"count": str(count), "amount": str(amount)},
        ))  # fmt: skip

    # 8. Refused attempts to change a locked or closed period
    since = today - timedelta(days=WINDOW_DAYS)
    attempts = (
        session.scalar(
            select(func.count())
            .select_from(SystemEvent)
            .where(
                SystemEvent.shop_id == shop_id,
                SystemEvent.code == finance_period_service.BLOCKED_EVENT_CODE,
                SystemEvent.created_at >= datetime.combine(since, time.min, tzinfo=UTC),
            )
        )
        or 0
    )
    if attempts:
        alerts.append(FinanceAlert(
            "PERIOD_CHANGE_REFUSED", "REVIEW", "Attempted change to a locked or closed period",
            f"{attempts} change(s) to a locked or closed period were refused in the last {WINDOW_DAYS} days. Review recommended.",
            {"attempts": str(attempts)},
        ))  # fmt: skip
    return alerts


def notify(session: Session, shop_id: int, today: date) -> int:
    """Put the current alerts in the shop's notification centre (once per alert per day: the existing
    notification service de-duplicates). Returns how many alerts were offered."""
    from app.services import notification_service

    found = compute(session, shop_id, today)
    for a in found:
        notification_service.emit_safely(
            session, shop_id, "BUSINESS_ALERT", title=a.title, message=a.message,
            dedupe_key=f"finance-alert:{a.code}:{today.isoformat()}", entity_type="finance_alert",
        )  # fmt: skip
    return len(found)
