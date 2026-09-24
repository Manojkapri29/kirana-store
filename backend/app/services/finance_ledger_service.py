"""The financial ledger: ONE normalised view over every money event, plus the few events finance owns itself.

There is deliberately no second accounting universe. Sales, quick sales, purchases, returns and khata payments
stay in the tables that own them; `movements()` reads them where they are and returns them in one shape, so a
row here can always be traced to its source document (`source_type` + `source_id`). What those tables cannot
express - an expense posting, a supplier payment, owner capital or withdrawal, other income, an adjustment -
is recorded as an INSERT-ONLY `FinanceEntry`. History is never edited: a correction is a reversal or an
adjustment, and posting the same source twice does nothing the second time (idempotent).

For every row two amounts matter and are kept apart:

* `amount`: the value of the event (a sale of 500, an expense of 200);
* `settled_amount`: the money that actually moved because of it (a 500 sale paid 300 in cash and 200 on credit
  settles 300; the 200 lives in the customer's khata). Cash reports use this, never `amount`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import (
    ApprovalRequest,
    Customer,
    FinanceEntry,
    Purchase,
    PurchaseReturn,
    QuickSale,
    Sale,
    SalesReturn,
    Supplier,
)
from app.models.enums import (
    ApprovalStatus,
    CashFlowClass,
    DocumentStatus,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PurchaseStatus,
    SaleStatus,
)
from app.services import approval_service, finance_period_service, khata_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.finance_settings_service import get_settings

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
ADJUSTMENT_APPROVAL_KIND = "FINANCE_ADJUSTMENT"
NOT_RECORDED = "NOT_RECORDED"

_DEFAULT_CLASS = {
    FinanceEventType.SALE: CashFlowClass.OPERATING,
    FinanceEventType.PURCHASE: CashFlowClass.OPERATING,
    FinanceEventType.SALE_RETURN: CashFlowClass.OPERATING,
    FinanceEventType.PURCHASE_RETURN: CashFlowClass.OPERATING,
    FinanceEventType.CUSTOMER_PAYMENT: CashFlowClass.OPERATING,
    FinanceEventType.SUPPLIER_PAYMENT: CashFlowClass.OPERATING,
    FinanceEventType.EXPENSE: CashFlowClass.OPERATING,
    FinanceEventType.OTHER_INCOME: CashFlowClass.OPERATING,
    FinanceEventType.OWNER_CAPITAL: CashFlowClass.FINANCING,
    FinanceEventType.OWNER_WITHDRAWAL: CashFlowClass.FINANCING,
    # ADJUSTMENT is deliberately absent: an adjustment has no justified class and is reported as unclassified.
}


@dataclass(frozen=True)
class LedgerRow:
    key: str
    entry_date: date
    event_type: FinanceEventType
    source_module: str
    source_type: str
    source_id: int
    reference: str | None
    amount: Decimal
    settled_amount: Decimal
    direction: FlowDirection
    payment_method: str  # a payment method value, or NOT_RECORDED when the source did not say
    customer_id: int | None
    supplier_id: int | None
    status: str  # POSTED, REVERSED (something reverses it) or REVERSAL (it reverses something)
    created_by: int | None
    cash_flow_class: CashFlowClass | None  # None = unclassified
    note: str | None = None


def money(value: object, *, field: str = "amount") -> Decimal:
    """A positive amount with at most two decimals. Floats and text are refused: no binary rounding."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal | int):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field)
    amount = Decimal(value)
    if not amount.is_finite() or amount <= 0:
        raise InvalidInputError("The amount must be greater than zero.", field=field)
    if amount != amount.quantize(CENT):
        raise InvalidInputError("Use at most 2 decimal places.", field=field)
    return amount.quantize(CENT)


def _method(value: object) -> str:
    return value.value if hasattr(value, "value") else (str(value) if value else NOT_RECORDED)  # type: ignore[union-attr]


# --- Recording (finance-owned events) -----------------------------------------------------------------------


def find_entry(
    session: Session, shop_id: int, event_type: FinanceEventType, reference_type: str, reference_id: int
) -> FinanceEntry | None:
    return session.scalar(
        select(FinanceEntry).where(
            FinanceEntry.shop_id == shop_id, FinanceEntry.event_type == event_type,
            FinanceEntry.reference_type == reference_type, FinanceEntry.reference_id == reference_id,
        )
    )  # fmt: skip


def get_entry(session: Session, shop_id: int, entry_id: int) -> FinanceEntry:
    entry = session.scalar(
        select(FinanceEntry).where(FinanceEntry.shop_id == shop_id, FinanceEntry.id == entry_id)
    )
    if entry is None:
        raise NotFoundError("Ledger entry not found")
    return entry


def record_entry(
    session: Session, ctx: RequestContext, *, event_type: FinanceEventType, direction: FlowDirection,
    amount: Decimal, payment_method: FinancePaymentMethod, entry_date: date,
    reference_type: str | None = None, reference_id: int | None = None, supplier_id: int | None = None,
    customer_id: int | None = None, note: str | None = None, cash_flow_class: CashFlowClass | None = None,
    controlled: bool = False, action: str = "record",
) -> tuple[FinanceEntry, bool]:  # fmt: skip
    """Insert one entry and return `(entry, created)`. When the same source (`event_type`, `reference_type`,
    `reference_id`) was already recorded this returns the existing row with `created=False` instead of
    posting twice. Refused when `entry_date` falls in a locked (unless `controlled`) or closed period."""
    amount = money(amount)
    if (
        supplier_id is not None
        and session.scalar(
            select(Supplier.id).where(Supplier.shop_id == ctx.shop_id, Supplier.id == supplier_id)
        )
        is None
    ):
        raise NotFoundError(
            "Supplier not found"
        )  # another shop's supplier is "not found", never a database error
    if (
        customer_id is not None
        and session.scalar(
            select(Customer.id).where(Customer.shop_id == ctx.shop_id, Customer.id == customer_id)
        )
        is None
    ):
        raise NotFoundError("Customer not found")
    if (reference_type is None) != (reference_id is None):
        raise InvalidInputError("A reference needs both a type and an id.", field="reference_id")
    if reference_type is not None and reference_id is not None:
        existing = find_entry(session, ctx.shop_id, event_type, reference_type, reference_id)
        if existing is not None:
            return existing, False
    finance_period_service.assert_open(session, ctx, entry_date, action=action, controlled=controlled)
    entry = FinanceEntry(
        shop_id=ctx.shop_id, event_type=event_type, direction=direction, amount=amount,
        payment_method=payment_method, entry_date=entry_date, reference_type=reference_type,
        reference_id=reference_id, supplier_id=supplier_id, customer_id=customer_id, note=note,
        cash_flow_class=cash_flow_class, created_by=ctx.user_id,
    )  # fmt: skip
    try:
        with session.begin_nested():
            session.add(entry)
            session.flush()
    except IntegrityError:
        # Lost a race with an identical posting: hand back the winner instead of failing.
        winner = (
            find_entry(session, ctx.shop_id, event_type, reference_type, reference_id)
            if reference_type is not None and reference_id is not None
            else None
        )
        if winner is None:
            raise
        return winner, False
    record_audit(
        session, ctx, entity_type="finance_entry", entity_id=entry.id, action="finance_entry_recorded",
        after={
            "event_type": event_type, "direction": direction, "amount": amount,
            "payment_method": payment_method,
            "entry_date": entry_date, "reference_type": reference_type, "reference_id": reference_id,
        },
    )  # fmt: skip
    return entry, True


def reverse_entry(
    session: Session, ctx: RequestContext, entry_id: int, reason: str, *, today: date
) -> tuple[FinanceEntry, bool]:  # fmt: skip
    """Cancel an entry by inserting its mirror image. Dated on the original's date while that period is open;
    if the original's period is locked or closed the reversal is dated `today` instead (a controlled
    correction in an open period), so a closed period is never altered. Reversing twice does nothing."""
    if not reason.strip():
        raise InvalidInputError("Give a reason for the reversal.", field="reason")
    original = get_entry(session, ctx.shop_id, entry_id)
    if original.reverses_entry_id is not None:
        raise ConflictError("A reversal cannot itself be reversed.")
    existing = session.scalar(
        select(FinanceEntry).where(
            FinanceEntry.shop_id == ctx.shop_id, FinanceEntry.reverses_entry_id == original.id
        )
    )
    if existing is not None:
        return existing, False
    original_status = finance_period_service.status_on(session, ctx.shop_id, original.entry_date)
    reversal_date = original.entry_date if original_status.value == "OPEN" else today
    finance_period_service.assert_open(session, ctx, reversal_date, action="reverse")
    mirror = FinanceEntry(
        shop_id=ctx.shop_id, event_type=original.event_type,
        direction=FlowDirection.IN if original.direction is FlowDirection.OUT else FlowDirection.OUT,
        amount=original.amount, payment_method=original.payment_method, entry_date=reversal_date,
        reference_type="REVERSAL", reference_id=original.id, supplier_id=original.supplier_id,
        customer_id=original.customer_id, cash_flow_class=original.cash_flow_class,
        reverses_entry_id=original.id, note=reason.strip(), created_by=ctx.user_id,
    )  # fmt: skip
    session.add(mirror)
    session.flush()
    record_audit(
        session, ctx, entity_type="finance_entry", entity_id=mirror.id, action="finance_entry_reversed",
        after={"reverses": original.id, "reason": reason.strip(), "entry_date": reversal_date},
    )  # fmt: skip
    return mirror, True


@dataclass(frozen=True)
class AdjustOutcome:
    """The recorded entry, or the id of the approval it is waiting on (never both)."""

    entry: FinanceEntry | None
    approval_request_id: int | None


def _adjustment_reason(direction: FlowDirection, amount: Decimal, on: date, method: str, note: str) -> str:
    return f"Finance adjustment: {direction.value} {amount} on {on.isoformat()} ({method}) - {note.strip()}"


def record_adjustment(
    session: Session, ctx: RequestContext, *, direction: FlowDirection, amount: Decimal,
    payment_method: FinancePaymentMethod, entry_date: date, note: str, approval_request_id: int | None = None,
) -> AdjustOutcome:  # fmt: skip
    """A manual financial or cash adjustment. It always needs a reason and may be dated inside a LOCKED
    period (never a closed one). Above the shop's configured limit it needs a second person's approval first:
    the requester redeems the approved request by sending it back with `approval_request_id`. An approval is
    single use and bound to this exact direction, amount, date, method and reason."""
    amount = money(amount)
    if not note.strip():
        raise InvalidInputError("Give a reason for this adjustment.", field="note")
    settings = get_settings(session, ctx.shop_id)
    limit = (
        settings.cash_adjustment_threshold
        if payment_method is FinancePaymentMethod.CASH
        else settings.adjustment_approval_threshold
    )
    needs_approval = limit is not None and amount > limit
    if not needs_approval:
        entry, _ = record_entry(
            session, ctx, event_type=FinanceEventType.ADJUSTMENT, direction=direction, amount=amount,
            payment_method=payment_method, entry_date=entry_date, note=note.strip(), controlled=True,
            action="adjust",
        )  # fmt: skip
        return AdjustOutcome(entry, None)
    finance_period_service.assert_open(session, ctx, entry_date, action="adjust", controlled=True)
    reason = _adjustment_reason(direction, amount, entry_date, payment_method.value, note)
    if approval_request_id is None:
        request = approval_service.create(
            session, ctx, kind=ADJUSTMENT_APPROVAL_KIND, entity_type="finance_adjustment",
            entity_id=ctx.shop_id,
            reason=reason, threshold_value=limit, observed_value=amount,
        )  # fmt: skip
        return AdjustOutcome(None, request.id)
    request: ApprovalRequest = approval_service.get(session, ctx.shop_id, approval_request_id)
    if request.kind != ADJUSTMENT_APPROVAL_KIND or request.reason != reason:
        raise InvalidInputError("That approval is not for this adjustment.", field="approval_request_id")
    if request.status is not ApprovalStatus.APPROVED:
        raise ConflictError("That adjustment has not been approved.")
    entry, created = record_entry(
        session, ctx, event_type=FinanceEventType.ADJUSTMENT, direction=direction, amount=amount,
        payment_method=payment_method, entry_date=entry_date, note=note.strip(), controlled=True,
        reference_type="APPROVAL", reference_id=request.id, action="adjust",
    )  # fmt: skip
    if not created:
        raise ConflictError("That approval has already been used.")
    return AdjustOutcome(entry, None)


# --- The normalised view -------------------------------------------------------------------------------


def _class_for(event: FinanceEventType, override: CashFlowClass | None) -> CashFlowClass | None:
    return override or _DEFAULT_CLASS.get(event)


def movements(session: Session, shop_id: int, start: date, end: date) -> list[LedgerRow]:
    """Every money event dated `start`..`end` (inclusive), newest first. Voided documents are excluded; a
    reversed finance entry stays visible together with the reversal that cancels it."""
    rows: list[LedgerRow] = []

    for s in session.scalars(
        select(Sale).where(
            Sale.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.sale_date >= start,
            Sale.sale_date <= end,
        )
    ):
        paid = s.total_amount if s.amount_paid is None else s.amount_paid
        rows.append(LedgerRow(
            f"sale:{s.id}", s.sale_date, FinanceEventType.SALE, "sales", "SALE", s.id, s.invoice_no,
            s.total_amount, paid, FlowDirection.IN, _method(s.payment_method), s.customer_id, None, "POSTED",
            s.posted_by or s.created_by, CashFlowClass.OPERATING,
        ))  # fmt: skip

    for q in session.scalars(
        select(QuickSale).where(
            QuickSale.shop_id == shop_id, QuickSale.status == SaleStatus.POSTED,
            QuickSale.sale_date >= start, QuickSale.sale_date <= end,
        )
    ):  # fmt: skip
        paid = q.total_amount if q.amount_paid is None else q.amount_paid
        rows.append(LedgerRow(
            f"quick_sale:{q.id}", q.sale_date, FinanceEventType.SALE, "quick_sales", "QUICK_SALE", q.id,
            q.quick_no, q.total_amount, paid, FlowDirection.IN, _method(q.payment_method), q.customer_id,
            None,
            "POSTED", q.posted_by or q.created_by, CashFlowClass.OPERATING,
        ))  # fmt: skip

    for r, customer_id in session.execute(
        select(SalesReturn, Sale.customer_id)
        .join(Sale, (Sale.shop_id == SalesReturn.shop_id) & (Sale.id == SalesReturn.sale_id))
        .where(
            SalesReturn.shop_id == shop_id, SalesReturn.status == DocumentStatus.POSTED,
            SalesReturn.return_date >= start, SalesReturn.return_date <= end,
        )
    ):  # fmt: skip
        refunded = r.total_refund if r.refund_mode.value in ("CASH", "UPI") else ZERO
        method = r.refund_mode.value if refunded else NOT_RECORDED
        rows.append(LedgerRow(
            f"sales_return:{r.id}", r.return_date, FinanceEventType.SALE_RETURN, "sales_returns",
            "SALES_RETURN", r.id, r.return_no, r.total_refund, refunded, FlowDirection.OUT, method,
            customer_id, None, "POSTED",
            r.created_by, CashFlowClass.OPERATING,
        ))  # fmt: skip

    for p in session.scalars(
        select(Purchase).where(
            Purchase.shop_id == shop_id, Purchase.status == PurchaseStatus.POSTED,
            Purchase.purchase_date >= start, Purchase.purchase_date <= end,
        )
    ):  # fmt: skip
        rows.append(LedgerRow(
            f"purchase:{p.id}", p.purchase_date, FinanceEventType.PURCHASE, "purchases", "PURCHASE", p.id,
            p.purchase_no, p.total_amount, p.amount_paid or ZERO, FlowDirection.OUT,
            _method(p.payment_method),
            None, p.supplier_id, "POSTED", p.posted_by or p.created_by, CashFlowClass.OPERATING,
        ))  # fmt: skip

    for r, supplier_id in session.execute(
        select(PurchaseReturn, Purchase.supplier_id)
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .where(
            PurchaseReturn.shop_id == shop_id, PurchaseReturn.status == DocumentStatus.POSTED,
            PurchaseReturn.return_date >= start, PurchaseReturn.return_date <= end,
        )
    ):  # fmt: skip
        refunded = r.total_amount if r.credit_mode.value in ("CASH", "UPI") else ZERO
        method = r.credit_mode.value if refunded else NOT_RECORDED
        rows.append(LedgerRow(
            f"purchase_return:{r.id}", r.return_date, FinanceEventType.PURCHASE_RETURN, "purchase_returns",
            "PURCHASE_RETURN", r.id, r.return_no, r.total_amount, refunded, FlowDirection.IN, method, None,
            supplier_id, "POSTED", r.created_by, CashFlowClass.OPERATING,
        ))  # fmt: skip

    for c in khata_service.list_payments(session, shop_id, start, end):
        rows.append(LedgerRow(
            f"khata_payment:{c.id}", c.entry_date, FinanceEventType.CUSTOMER_PAYMENT, "khata",
            "KHATA_PAYMENT", c.id, c.payment_reference, c.amount, c.amount, FlowDirection.IN,
            _method(c.payment_method), c.customer_id, None, "POSTED", c.created_by, CashFlowClass.OPERATING,
            c.note,
        ))  # fmt: skip

    entries = list(
        session.scalars(
            select(FinanceEntry).where(
                FinanceEntry.shop_id == shop_id,
                FinanceEntry.entry_date >= start,
                FinanceEntry.entry_date <= end,
            )
        )
    )
    reversed_entry_ids = set(
        session.scalars(
            select(FinanceEntry.reverses_entry_id).where(
                FinanceEntry.shop_id == shop_id, FinanceEntry.reverses_entry_id.is_not(None)
            )
        )
    )
    for e in entries:
        status = (
            "REVERSAL" if e.reverses_entry_id else ("REVERSED" if e.id in reversed_entry_ids else "POSTED")
        )
        rows.append(LedgerRow(
            f"entry:{e.id}", e.entry_date, e.event_type, "finance", "FINANCE_ENTRY", e.id,
            f"{e.reference_type}:{e.reference_id}" if e.reference_type else None, e.amount, e.amount,
            e.direction, e.payment_method.value, e.customer_id, e.supplier_id, status, e.created_by,
            _class_for(e.event_type, e.cash_flow_class), e.note,
        ))  # fmt: skip
    rows.sort(key=lambda r: (r.entry_date, r.key), reverse=True)
    return rows


def effective_settled(row: LedgerRow) -> Decimal:
    """The money a row really moved, for cash reports. A REVERSED entry and its REVERSAL both stay listed,
    but the pair nets to zero because they point in opposite directions."""
    return row.settled_amount


def signed_settled(row: LedgerRow) -> Decimal:
    return row.settled_amount if row.direction is FlowDirection.IN else -row.settled_amount


def list_ledger(
    session: Session, shop_id: int, start: date, end: date, *,
    event_types: Sequence[FinanceEventType] | None = None,
    payment_method: str | None = None, customer_id: int | None = None, supplier_id: int | None = None,
    status: str | None = None, reference: str | None = None, source_type: str | None = None,
) -> list[LedgerRow]:  # fmt: skip
    rows = movements(session, shop_id, start, end)
    if event_types:
        rows = [r for r in rows if r.event_type in event_types]
    if payment_method:
        rows = [r for r in rows if r.payment_method == payment_method]
    if customer_id is not None:
        rows = [r for r in rows if r.customer_id == customer_id]
    if supplier_id is not None:
        rows = [r for r in rows if r.supplier_id == supplier_id]
    if status:
        rows = [r for r in rows if r.status == status]
    if source_type:
        rows = [r for r in rows if r.source_type == source_type]
    if reference:
        needle = reference.strip().lower()
        rows = [r for r in rows if r.reference and needle in r.reference.lower()]
    return rows
