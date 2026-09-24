"""Accounts payable: what the shop owes each supplier, built from the purchases, returns and payments that
already exist. There is no separate supplier-balance ledger.

    payable = purchases - purchase returns - payments made + cash refunds received

(a return settled as supplier credit reduces what is owed; one settled in cash/UPI was money coming back, so
the refund is added back. Payments are `SUPPLIER_PAYMENT` finance entries, net of any reversal, plus anything
recorded on the purchase itself.)

Ageing methodology, stated on every report: suppliers have NO payment terms stored anywhere, so no due date is
invented. Instead each purchase is aged from its own transaction date, and payments and returns are applied to
the oldest purchases first (FIFO). Buckets are 0-30, 31-60, 61-90 and 90+ days.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FinanceEntry, Purchase, PurchaseReturn, Supplier
from app.models.enums import DocumentStatus, FinanceEventType, FlowDirection, PurchaseStatus

ZERO = Decimal("0.00")
BUCKETS = ("0-30", "31-60", "61-90", "90+")
METHODOLOGY = (
    "Suppliers have no payment terms recorded, so ageing uses each purchase's own date (not a due date). "
    "Payments and returns are applied to the oldest purchases first."
)


def bucket_of(age_days: int) -> str:
    if age_days <= 30:
        return BUCKETS[0]
    if age_days <= 60:
        return BUCKETS[1]
    if age_days <= 90:
        return BUCKETS[2]
    return BUCKETS[3]


def empty_buckets() -> dict[str, Decimal]:
    return {b: ZERO for b in BUCKETS}


def age_open_items(
    items: list[tuple[date, Decimal]], credits: Decimal, as_of: date
) -> tuple[dict[str, Decimal], Decimal]:
    """Apply `credits` to the oldest of `items` (date, amount) first; return the still-open amount per bucket
    and any credit left over (an advance)."""
    buckets = empty_buckets()
    remaining = credits
    for when, amount in sorted(items, key=lambda i: i[0]):
        used = min(amount, remaining) if remaining > 0 else ZERO
        remaining -= used
        open_amount = amount - used
        if open_amount > 0:
            buckets[bucket_of(max((as_of - when).days, 0))] += open_amount
    return buckets, max(remaining, ZERO)


@dataclass(frozen=True)
class SupplierPayable:
    supplier_id: int
    name: str
    total_purchases: Decimal
    purchase_returns: Decimal
    payments_made: Decimal
    refunds_received: Decimal
    balance: Decimal  # positive: the shop owes the supplier; negative: the supplier holds the shop's advance
    advance: Decimal
    aging: dict[str, Decimal]
    oldest_open_date: date | None


@dataclass(frozen=True)
class PayablesReport:
    as_of: date
    suppliers: list[SupplierPayable]
    total_payable: Decimal
    total_advances: Decimal
    total_purchases: Decimal
    total_payments: Decimal
    total_returns: Decimal
    aging: dict[str, Decimal]
    methodology: str = METHODOLOGY
    notes: list[str] = field(default_factory=list)


def compute(session: Session, shop_id: int, as_of: date, *, supplier_id: int | None = None) -> PayablesReport:
    purchase_query = select(Purchase).where(
        Purchase.shop_id == shop_id, Purchase.status == PurchaseStatus.POSTED, Purchase.purchase_date <= as_of
    )
    if supplier_id is not None:
        purchase_query = purchase_query.where(Purchase.supplier_id == supplier_id)
    purchases: dict[int, list[Purchase]] = {}
    for p in session.scalars(purchase_query):
        purchases.setdefault(p.supplier_id, []).append(p)

    returns: dict[int, list[PurchaseReturn]] = {}
    for r, sid in session.execute(
        select(PurchaseReturn, Purchase.supplier_id)
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .where(
            PurchaseReturn.shop_id == shop_id,
            PurchaseReturn.status == DocumentStatus.POSTED,
            PurchaseReturn.return_date <= as_of,
        )
    ):
        returns.setdefault(sid, []).append(r)

    paid: dict[int, Decimal] = {}
    for e in session.scalars(
        select(FinanceEntry).where(
            FinanceEntry.shop_id == shop_id,
            FinanceEntry.event_type == FinanceEventType.SUPPLIER_PAYMENT,
            FinanceEntry.entry_date <= as_of,
            FinanceEntry.supplier_id.is_not(None),
        )
    ):
        signed = e.amount if e.direction is FlowDirection.OUT else -e.amount
        paid[e.supplier_id] = paid.get(e.supplier_id, ZERO) + signed

    names = {s.id: s.name for s in session.scalars(select(Supplier).where(Supplier.shop_id == shop_id))}
    rows: list[SupplierPayable] = []
    totals_aging = empty_buckets()
    for sid in sorted(set(purchases) | set(paid)):
        if supplier_id is not None and sid != supplier_id:
            continue
        bought = purchases.get(sid, [])
        total_purchases = sum((p.total_amount for p in bought), ZERO)
        returned = sum((r.total_amount for r in returns.get(sid, [])), ZERO)
        refunds = sum(
            (r.total_amount for r in returns.get(sid, []) if r.credit_mode.value in ("CASH", "UPI")), ZERO
        )
        payments = paid.get(sid, ZERO) + sum((p.amount_paid or ZERO for p in bought), ZERO)
        balance = total_purchases - returned - payments + refunds
        aging, advance = age_open_items(
            [(p.purchase_date, p.total_amount) for p in bought], returned + payments - refunds, as_of
        )
        open_dates = [p.purchase_date for p in bought] if balance > 0 else []
        for b, v in aging.items():
            totals_aging[b] += v
        rows.append(SupplierPayable(
            sid, names.get(sid, "?"), total_purchases, returned, payments, refunds, balance, advance, aging,
            min(open_dates) if open_dates else None,
        ))  # fmt: skip
    rows.sort(key=lambda r: (-r.balance, r.name))
    return PayablesReport(
        as_of=as_of, suppliers=rows,
        total_payable=sum((r.balance for r in rows if r.balance > 0), ZERO),
        total_advances=sum((-r.balance for r in rows if r.balance < 0), ZERO),
        total_purchases=sum((r.total_purchases for r in rows), ZERO),
        total_payments=sum((r.payments_made for r in rows), ZERO),
        total_returns=sum((r.purchase_returns for r in rows), ZERO),
        aging=totals_aging,
    )  # fmt: skip
