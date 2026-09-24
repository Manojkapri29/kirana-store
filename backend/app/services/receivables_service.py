"""Accounts receivable: what customers owe the shop. KHATA IS THE SOURCE OF TRUTH: every balance here comes
from `khata_service` (no second customer-balance system), and ageing reads the same ledger through its
read-only helpers.

Ageing methodology, stated on every report: no due dates exist in the system, so nothing is called "overdue"
by a due date. Each charge (credit sale, opening balance, positive adjustment) is aged from its own transaction
date and payments, return credits and negative adjustments are applied to the oldest charges first (FIFO).
Buckets are 0-30, 31-60, 61-90 and 90+ days. The alert for "aged beyond N days" uses the shop's own setting, not
an invented term.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer
from app.models.enums import CustomerLedgerEntryType
from app.services import khata_service
from app.services.payables_service import BUCKETS, bucket_of, empty_buckets

ZERO = Decimal("0.00")
METHODOLOGY = (
    "No due dates are recorded, so ageing uses each charge's own date (not a due date). Payments and credits "
    "are applied to the oldest charges first. Khata remains the source of truth for every balance."
)


@dataclass(frozen=True)
class CustomerReceivable:
    customer_id: int
    name: str
    balance: Decimal  # positive: the customer owes; negative: the customer has paid in advance
    advance: Decimal
    aging: dict[str, Decimal]
    oldest_open_date: date | None
    oldest_open_days: int | None
    last_payment_date: date | None


@dataclass(frozen=True)
class ReceivablesReport:
    as_of: date
    customers: list[CustomerReceivable]
    total_receivables: Decimal
    total_advances: Decimal
    aging: dict[str, Decimal]
    credit_sales: Decimal  # in the period
    payments_received: Decimal
    return_credits: Decimal
    period_start: date | None
    period_end: date | None
    methodology: str = METHODOLOGY
    overdue: str = "Not Available"  # no due-date data exists
    notes: list[str] = field(default_factory=list)


def age_ledger(
    events: list[khata_service.AgingEvent], as_of: date
) -> dict[int, tuple[dict[str, Decimal], Decimal, date | None]]:
    """Per customer: still-open amount per bucket, any advance, and the oldest date still open (FIFO)."""
    open_items: dict[int, list[list]] = {}
    credit: dict[int, Decimal] = {}
    for ev in events:
        items = open_items.setdefault(ev.customer_id, [])
        credit.setdefault(ev.customer_id, ZERO)
        amount = ev.amount_delta
        if amount > 0:
            absorbed = min(amount, credit[ev.customer_id])
            credit[ev.customer_id] -= absorbed
            if amount - absorbed > 0:
                items.append([ev.entry_date, amount - absorbed])
        elif amount < 0:
            left = -amount
            for item in items:
                if left <= 0:
                    break
                used = min(item[1], left)
                item[1] -= used
                left -= used
            credit[ev.customer_id] += left
    out = {}
    for cid, items in open_items.items():
        buckets = empty_buckets()
        oldest = None
        for when, amount in items:
            if amount > 0:
                buckets[bucket_of(max((as_of - when).days, 0))] += amount
                oldest = when if oldest is None or when < oldest else oldest
        out[cid] = (buckets, credit.get(cid, ZERO), oldest)
    return out


def compute(
    session: Session,
    shop_id: int,
    as_of: date,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> ReceivablesReport:
    events = khata_service.aging_events(session, shop_id, as_of)
    aged = age_ledger(events, as_of)
    ids = sorted({e.customer_id for e in events})
    balances = khata_service.get_balance_map(session, shop_id, ids) if ids else {}
    last_paid = khata_service.last_payment_dates(session, shop_id, ids) if ids else {}
    names = (
        {
            c.id: c.name
            for c in session.scalars(
                select(Customer).where(Customer.shop_id == shop_id, Customer.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    rows: list[CustomerReceivable] = []
    total_aging = empty_buckets()
    for cid in ids:
        balance = balances.get(cid, ZERO)
        buckets, advance, oldest = aged.get(cid, (empty_buckets(), ZERO, None))
        if balance == 0 and advance == 0:
            continue
        if balance > 0:
            for b, v in buckets.items():
                total_aging[b] += v
        rows.append(CustomerReceivable(
            cid, names.get(cid, "?"), balance, advance, buckets, oldest if balance > 0 else None,
            (as_of - oldest).days if (oldest and balance > 0) else None, last_paid.get(cid),
        ))  # fmt: skip
    rows.sort(key=lambda r: (-r.balance, r.name))
    credit_sales = payments = returns = ZERO
    if period_start and period_end:
        totals = khata_service.period_totals(session, shop_id, period_start, period_end)
        credit_sales = totals.get(CustomerLedgerEntryType.CREDIT_SALE, ZERO)
        payments = -totals.get(CustomerLedgerEntryType.PAYMENT, ZERO)
        returns = -totals.get(CustomerLedgerEntryType.RETURN_CREDIT, ZERO)
    return ReceivablesReport(
        as_of=as_of, customers=rows,
        total_receivables=sum((r.balance for r in rows if r.balance > 0), ZERO),
        total_advances=sum((-r.balance for r in rows if r.balance < 0), ZERO),
        aging=total_aging, credit_sales=credit_sales, payments_received=payments, return_credits=returns,
        period_start=period_start, period_end=period_end,
    )  # fmt: skip


__all__ = ["BUCKETS", "compute", "age_ledger", "ReceivablesReport", "CustomerReceivable"]
