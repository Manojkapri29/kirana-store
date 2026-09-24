"""Daily cash: what SHOULD be in the drawer, and what was actually counted.

    Opening cash
    + cash sales + customer payments + other cash income + owner capital + supplier refunds
    - cash purchases - expenses - supplier payments - refunds to customers - owner withdrawals
    +/- adjustments
    = expected closing cash

Only money that really moved in CASH counts: the settled part of a sale (a credit sale's unpaid part is in the
customer's khata, not the drawer), paid through the ledger view in `finance_ledger_service`. A payment whose
method was never recorded is NOT assumed to be cash: it is listed as unclassified instead.

Opening cash is never guessed. It is the actual cash of the latest physical count before the day, rolled forward
by the cash movements since; with no earlier count it is "Not Available" (None) and so is the expected closing.
A correction is never a silent edit of the expected figure: it is an explicit adjustment (with a reason, a user
and an audit row) recorded through `finance_ledger_service.record_adjustment`.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import CashCount
from app.models.enums import FinanceEventType, FlowDirection
from app.services import finance_ledger_service, finance_period_service
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError
from app.services.finance_ledger_service import LedgerRow

ZERO = Decimal("0.00")
CASH = "CASH"
NOT_AVAILABLE = "Not Available"

# (line key, event type, direction of the money). Adjustments and reversals are folded in by sign below.
LINES = (
    ("cash_sales", FinanceEventType.SALE),
    ("customer_payments", FinanceEventType.CUSTOMER_PAYMENT),
    ("other_cash_income", FinanceEventType.OTHER_INCOME),
    ("owner_capital", FinanceEventType.OWNER_CAPITAL),
    ("supplier_refunds", FinanceEventType.PURCHASE_RETURN),
    ("cash_purchases", FinanceEventType.PURCHASE),
    ("expenses", FinanceEventType.EXPENSE),
    ("supplier_payments", FinanceEventType.SUPPLIER_PAYMENT),
    ("refunds", FinanceEventType.SALE_RETURN),
    ("owner_withdrawals", FinanceEventType.OWNER_WITHDRAWAL),
    ("adjustments", FinanceEventType.ADJUSTMENT),
)


@dataclass(frozen=True)
class CashCountRow:
    id: int
    count_date: date
    expected_cash: Decimal | None
    actual_cash: Decimal
    difference: Decimal | None
    reason: str | None
    counted_by: int
    counted_at: object


@dataclass(frozen=True)
class CashSummary:
    day: date
    opening_cash: Decimal | None
    lines: dict[str, Decimal]  # signed: money in is positive, money out is negative
    net_movement: Decimal
    expected_closing: Decimal | None
    opening_basis: str  # how the opening figure was derived, in words
    unclassified_receipts: Decimal  # money received with no payment method recorded (NOT counted as cash)
    latest_count: CashCountRow | None
    notes: list[str] = field(default_factory=list)


def _signed(row: LedgerRow) -> Decimal:
    return row.settled_amount if row.direction is FlowDirection.IN else -row.settled_amount


def _cash_rows(rows: list[LedgerRow]) -> list[LedgerRow]:
    return [r for r in rows if r.payment_method == CASH and r.settled_amount > 0]


def net_cash(session: Session, shop_id: int, start: date, end: date) -> Decimal:
    if start > end:
        return ZERO
    return sum(
        (_signed(r) for r in _cash_rows(finance_ledger_service.movements(session, shop_id, start, end))), ZERO
    )


def _view(c: CashCount) -> CashCountRow:
    return CashCountRow(
        c.id, c.count_date, c.expected_cash, c.actual_cash, c.difference, c.reason, c.counted_by, c.counted_at
    )


def latest_count_before(session: Session, shop_id: int, day: date) -> CashCount | None:
    return session.scalar(
        select(CashCount)
        .where(CashCount.shop_id == shop_id, CashCount.count_date < day)
        .order_by(CashCount.count_date.desc(), CashCount.id.desc())
        .limit(1)
    )


def latest_count_on(session: Session, shop_id: int, day: date) -> CashCount | None:
    return session.scalar(
        select(CashCount)
        .where(CashCount.shop_id == shop_id, CashCount.count_date == day)
        .order_by(CashCount.id.desc())
        .limit(1)
    )


def daily_summary(session: Session, shop_id: int, day: date) -> CashSummary:
    baseline = latest_count_before(session, shop_id, day)
    if baseline is None:
        opening: Decimal | None = None
        basis = "No cash count exists before this day, so the opening cash cannot be known. Record a count to set it."
    else:
        opening = baseline.actual_cash + net_cash(
            session, shop_id, baseline.count_date + timedelta(days=1), day - timedelta(days=1)
        )
        basis = (
            f"Counted cash of {baseline.actual_cash} on {baseline.count_date}, plus the cash movements since."
        )

    day_rows = finance_ledger_service.movements(session, shop_id, day, day)
    lines = {key: ZERO for key, _ in LINES}
    for key, event in LINES:
        lines[key] = sum((_signed(r) for r in _cash_rows(day_rows) if r.event_type is event), ZERO)
    # Money out is shown as a negative figure, in line with the formula above.
    unclassified = sum(
        (r.settled_amount for r in day_rows if r.payment_method == finance_ledger_service.NOT_RECORDED
         and r.direction is FlowDirection.IN and r.settled_amount > 0),
        ZERO,
    )  # fmt: skip
    net = sum(lines.values(), ZERO)
    latest = latest_count_on(session, shop_id, day)
    notes = []
    if unclassified > 0:
        notes.append(
            f"{unclassified} was received with no payment method recorded and is not counted as cash."
        )
    return CashSummary(
        day=day, opening_cash=opening, lines=lines, net_movement=net,
        expected_closing=None if opening is None else opening + net, opening_basis=basis,
        unclassified_receipts=unclassified, latest_count=_view(latest) if latest else None, notes=notes,
    )  # fmt: skip


def record_count(
    session: Session, ctx: RequestContext, *, count_date: date, actual_cash: Decimal, reason: str | None, today: date
) -> CashCount:  # fmt: skip
    """Record a physical count. The expected figure is taken from the books at that moment and stored with the
    count, so a later correction never rewrites what was expected when the drawer was counted."""
    if (
        isinstance(actual_cash, bool)
        or isinstance(actual_cash, float)
        or not isinstance(actual_cash, Decimal | int)
    ):
        raise InvalidInputError("Enter the counted cash as a number.", field="actual_cash")
    actual = Decimal(actual_cash)
    if actual < 0 or actual != actual.quantize(Decimal("0.01")):
        raise InvalidInputError(
            "Enter the counted cash with at most 2 decimals, not below zero.", field="actual_cash"
        )
    if count_date > today:
        raise InvalidInputError("A cash count cannot be dated in the future.", field="count_date")
    finance_period_service.assert_open(session, ctx, count_date, action="cash_count", controlled=True)
    expected = daily_summary(session, ctx.shop_id, count_date).expected_closing
    difference = None if expected is None else actual - expected
    if difference is not None and difference != 0 and not (reason or "").strip():
        raise InvalidInputError("The count differs from the expected cash: say why.", field="reason")
    count = CashCount(
        shop_id=ctx.shop_id, count_date=count_date, expected_cash=expected, actual_cash=actual.quantize(Decimal("0.01")),
        difference=difference, reason=(reason or "").strip() or None, counted_by=ctx.user_id, counted_at=utc_now(),
    )  # fmt: skip
    session.add(count)
    session.flush()
    record_audit(
        session, ctx, entity_type="cash_count", entity_id=count.id, action="cash_counted",
        after={"count_date": count_date, "expected": expected, "actual": actual, "difference": difference,
               "reason": count.reason},
    )  # fmt: skip
    return count


def list_counts(
    session: Session, shop_id: int, *, date_from: date | None = None, date_to: date | None = None, limit: int = 100
) -> list[CashCountRow]:  # fmt: skip
    query = select(CashCount).where(CashCount.shop_id == shop_id)
    if date_from:
        query = query.where(CashCount.count_date >= date_from)
    if date_to:
        query = query.where(CashCount.count_date <= date_to)
    return [
        _view(c)
        for c in session.scalars(
            query.order_by(CashCount.count_date.desc(), CashCount.id.desc()).limit(limit)
        )
    ]
