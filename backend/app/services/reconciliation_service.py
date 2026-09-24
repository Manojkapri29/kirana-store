"""Reconciliation foundation: reviewing the shop's own electronic payment records.

There is NO bank integration and NO bank data anywhere in this system, so nothing is ever matched to a bank
statement automatically and no bank transaction is ever invented: the report says "Bank Integration Not
Configured". What this does offer is a reviewed status per payment record (a UPI/card/bank-transfer/other sale
payment, a customer payment, a supplier payment, an electronic refund or expense), set by an authorised person
after they have checked it against their own statement:

    UNMATCHED        the default: nobody has confirmed it
    MATCHED          confirmed in full (the confirmed amount is stored)
    PARTIAL          confirmed for less than the recorded amount
    REVIEW_REQUIRED  flagged for a second look

Cash is not reconciled here: it is checked by physical cash counts. Marks are insert-only history (the newest is
the status); every mark is audited with who, when, the amount and the note.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import (
    FinanceEntry,
    Purchase,
    PurchaseReturn,
    QuickSale,
    ReconciliationMark,
    Sale,
    SalesReturn,
)
from app.models.enums import ReconStatus
from app.services import finance_ledger_service, khata_service
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError, NotFoundError
from app.services.finance_ledger_service import LedgerRow

ZERO = Decimal("0.00")
BANK_NOTE = "Bank Integration Not Configured"
CASH = "CASH"


@dataclass(frozen=True)
class ReconItem:
    row: LedgerRow
    status: ReconStatus
    confirmed_amount: Decimal | None
    note: str | None
    marked_by: int | None
    marked_at: datetime | None


@dataclass(frozen=True)
class ReconSummary:
    date_from: date
    date_to: date
    counts: dict[str, int]
    amounts: dict[str, Decimal]
    total_items: int
    bank_integration: str
    items: list[ReconItem]
    notes: list[str] = field(default_factory=list)


def _latest_marks(session: Session, shop_id: int) -> dict[tuple[str, int], ReconciliationMark]:
    latest: dict[tuple[str, int], ReconciliationMark] = {}
    for m in session.scalars(
        select(ReconciliationMark)
        .where(ReconciliationMark.shop_id == shop_id)
        .order_by(ReconciliationMark.id)
    ):
        latest[(m.source_type, m.source_id)] = m
    return latest


def reconcilable(rows: list[LedgerRow]) -> list[LedgerRow]:
    """Electronic money that actually moved. Cash is excluded (it has cash counts)."""
    return [r for r in rows if r.settled_amount > 0 and r.payment_method != CASH]


def summary(
    session: Session, shop_id: int, date_from: date, date_to: date, *, status: ReconStatus | None = None,
    payment_method: str | None = None,
) -> ReconSummary:  # fmt: skip
    marks = _latest_marks(session, shop_id)
    items: list[ReconItem] = []
    for r in reconcilable(finance_ledger_service.movements(session, shop_id, date_from, date_to)):
        if payment_method and r.payment_method != payment_method:
            continue
        m = marks.get((r.source_type, r.source_id))
        item = ReconItem(
            r,
            m.status if m else ReconStatus.UNMATCHED,
            m.confirmed_amount if m else None,
            m.note if m else None,
            m.created_by if m else None,
            m.created_at if m else None,
        )
        if status is None or item.status is status:
            items.append(item)
    counts = {s.value: 0 for s in ReconStatus}
    amounts = {s.value: ZERO for s in ReconStatus}
    for i in items:
        counts[i.status.value] += 1
        amounts[i.status.value] += i.row.settled_amount
    notes = [BANK_NOTE + ": nothing is matched to a bank statement automatically."]
    if any(i.row.payment_method == finance_ledger_service.NOT_RECORDED for i in items):
        notes.append("Some records have no payment method recorded.")
    return ReconSummary(date_from, date_to, counts, amounts, len(items), BANK_NOTE, items, notes)


def _source_row(
    session: Session, shop_id: int, source_type: str, source_id: int
) -> tuple[date, Decimal, str] | None:
    """(date, settled amount, method) of a source record, or None if it does not exist in this shop."""
    if source_type == "SALE":
        s = session.scalar(
            select(Sale).where(Sale.shop_id == shop_id, Sale.id == source_id, Sale.status == "POSTED")
        )
        if s:
            paid = s.total_amount if s.amount_paid is None else s.amount_paid
            return s.sale_date, paid, finance_ledger_service._method(s.payment_method)  # noqa: SLF001
    elif source_type == "QUICK_SALE":
        q = session.scalar(
            select(QuickSale).where(
                QuickSale.shop_id == shop_id, QuickSale.id == source_id, QuickSale.status == "POSTED"
            )
        )
        if q:
            paid = q.total_amount if q.amount_paid is None else q.amount_paid
            return q.sale_date, paid, finance_ledger_service._method(q.payment_method)  # noqa: SLF001
    elif source_type == "PURCHASE":
        p = session.scalar(
            select(Purchase).where(
                Purchase.shop_id == shop_id, Purchase.id == source_id, Purchase.status == "POSTED"
            )
        )
        if p:
            return p.purchase_date, p.amount_paid or ZERO, finance_ledger_service._method(p.payment_method)  # noqa: SLF001
    elif source_type == "SALES_RETURN":
        r = session.scalar(
            select(SalesReturn).where(
                SalesReturn.shop_id == shop_id, SalesReturn.id == source_id, SalesReturn.status == "POSTED"
            )
        )
        if r and r.refund_mode.value in ("CASH", "UPI"):
            return r.return_date, r.total_refund, r.refund_mode.value
    elif source_type == "PURCHASE_RETURN":
        r = session.scalar(
            select(PurchaseReturn).where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.id == source_id,
                PurchaseReturn.status == "POSTED",
            )
        )
        if r and r.credit_mode.value in ("CASH", "UPI"):
            return r.return_date, r.total_amount, r.credit_mode.value
    elif source_type == "KHATA_PAYMENT":
        k = khata_service.get_payment(session, shop_id, source_id)
        if k:
            return k.entry_date, k.amount, finance_ledger_service._method(k.payment_method)  # noqa: SLF001
    elif source_type == "FINANCE_ENTRY":
        e = session.scalar(
            select(FinanceEntry).where(FinanceEntry.shop_id == shop_id, FinanceEntry.id == source_id)
        )
        if e:
            return e.entry_date, e.amount, e.payment_method.value
    return None


def mark(
    session: Session, ctx: RequestContext, *, source_type: str, source_id: int, status: ReconStatus,
    confirmed_amount: Decimal | None = None, note: str | None = None,
) -> ReconciliationMark:  # fmt: skip
    """Record a review of one payment record. Audited. Marking never changes the record itself."""
    found = _source_row(session, ctx.shop_id, source_type, source_id)
    if found is None:
        raise NotFoundError("Payment record not found")
    _, amount, method = found
    if method == CASH or amount <= 0:
        raise InvalidInputError(
            "Only electronic payments are reconciled here; cash is checked by cash counts.", field="source_id"
        )
    note = (note or "").strip() or None
    confirmed: Decimal | None = None
    if status in (ReconStatus.MATCHED, ReconStatus.PARTIAL):
        confirmed = (
            amount if (status is ReconStatus.MATCHED and confirmed_amount is None) else confirmed_amount
        )
        if confirmed is None:
            raise InvalidInputError("Say how much was confirmed.", field="confirmed_amount")
        finance_ledger_service.money(confirmed, field="confirmed_amount")
        if status is ReconStatus.MATCHED and confirmed != amount:
            raise InvalidInputError(
                "A matched record is confirmed for its full amount; use PARTIAL for less.",
                field="confirmed_amount",
            )
        if status is ReconStatus.PARTIAL and confirmed >= amount:
            raise InvalidInputError(
                "A partial match is for less than the recorded amount.", field="confirmed_amount"
            )
    elif confirmed_amount is not None:
        raise InvalidInputError(
            "A confirmed amount only applies to MATCHED or PARTIAL.", field="confirmed_amount"
        )
    if status is ReconStatus.REVIEW_REQUIRED and not note:
        raise InvalidInputError("Say what needs a second look.", field="note")
    previous = _latest_marks(session, ctx.shop_id).get((source_type, source_id))
    row = ReconciliationMark(
        shop_id=ctx.shop_id, source_type=source_type, source_id=source_id, status=status,
        confirmed_amount=confirmed, note=note, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(row)
    session.flush()
    record_audit(
        session, ctx, entity_type="reconciliation", entity_id=row.id, action="reconciliation_marked",
        before={"status": previous.status if previous else ReconStatus.UNMATCHED},
        after={"source_type": source_type, "source_id": source_id, "status": status, "confirmed_amount": confirmed,
               "note": note},
    )  # fmt: skip
    return row


def history(session: Session, shop_id: int, source_type: str, source_id: int) -> list[ReconciliationMark]:
    return list(
        session.scalars(
            select(ReconciliationMark)
            .where(
                ReconciliationMark.shop_id == shop_id,
                ReconciliationMark.source_type == source_type,
                ReconciliationMark.source_id == source_id,
            )
            .order_by(ReconciliationMark.id.desc())
        )
    )


def unreconciled_count(session: Session, shop_id: int, date_from: date, date_to: date) -> tuple[int, Decimal]:
    s = summary(session, shop_id, date_from, date_to)
    open_items = [i for i in s.items if i.status is not ReconStatus.MATCHED]
    return len(open_items), sum((i.row.settled_amount for i in open_items), ZERO)
