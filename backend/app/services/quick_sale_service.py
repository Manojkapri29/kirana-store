"""Quick/Daily Sales: money-only entries for days when a bill is not made product by product.

Generic for every kind of business. A Quick Sale has **no product lines**, so by construction it has:
  * no inventory effect: this module does not import `inventory_service` and the table has no product or
    quantity column (a test guards both);
  * no cost of goods and no product-level profit: profit is "Not Available" (BUSINESS_RULES S2, F2). The
    entry says nothing about what was sold, so nothing is invented;
  * no product-level discount and no promotion engine. The only discount is one optional amount off the
    whole entry (`gross_amount - discount = total_amount`), recorded as a transaction-level adjustment.

Lifecycle, like a detailed sale: DRAFT -> POSTED -> VOID. A draft is an entry being prepared and affects
nothing. Posting numbers it (QS/2026-27/0001), settles the payment and charges any unpaid part to the
customer's khata through `khata_service` (reference QUICK_SALE), all in one transaction. A posted entry is
never edited or deleted; voiding reverses the khata charge and keeps the record. To correct one, void it and
enter a new one (BUSINESS_RULES S6). Every query is scoped to the caller's shop.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Customer, QuickSale, User
from app.models.enums import KhataReferenceType, PaymentMethod, PaymentType, SaleStatus
from app.services import (
    entitlement_service,
    khata_service,
    loyalty_service,
    numbering_service,
    payment_service,
    referral_service,
)
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DOC_TYPE = "QUICK_SALE"
NUMBER_PREFIX = "QS"
ZERO = Decimal("0.00")
NOT_AVAILABLE = "Not Available"  # what a quick sale's profit is, everywhere it is shown


@dataclass(frozen=True)
class QuickSaleView:
    sale: QuickSale
    customer_name: str | None
    created_by_name: str
    posted_by_name: str | None

    @property
    def credit_amount(self) -> Decimal:
        """The part of a posted entry that is on the customer's khata."""
        sale = self.sale
        if sale.amount_paid is None or sale.total_amount <= sale.amount_paid:
            return ZERO
        return sale.total_amount - sale.amount_paid


@dataclass(frozen=True)
class QuickSaleRow:
    sale: QuickSale
    customer_name: str | None
    created_by_name: str


# --- Reading -------------------------------------------------------------------------------------


def _get(session: Session, shop_id: int, quick_sale_id: int, *, lock: bool = False) -> QuickSale:
    query = select(QuickSale).where(QuickSale.shop_id == shop_id, QuickSale.id == quick_sale_id)
    sale = session.scalar(query.with_for_update() if lock else query)
    if sale is None:
        raise NotFoundError("Quick sale not found")  # also the answer for another shop's entry
    return sale


def get_quick_sale_view(session: Session, shop_id: int, quick_sale_id: int) -> QuickSaleView:
    posted_by = aliased(User)
    row = session.execute(
        select(QuickSale, Customer.name, User.full_name, posted_by.full_name)
        .outerjoin(Customer, (Customer.shop_id == QuickSale.shop_id) & (Customer.id == QuickSale.customer_id))
        .join(User, (User.shop_id == QuickSale.shop_id) & (User.id == QuickSale.created_by))
        .outerjoin(
            posted_by, (posted_by.shop_id == QuickSale.shop_id) & (posted_by.id == QuickSale.posted_by)
        )
        .where(QuickSale.shop_id == shop_id, QuickSale.id == quick_sale_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise NotFoundError("Quick sale not found")
    return QuickSaleView(*row)


def _filters(
    shop_id: int,
    *,
    q: str | None,
    customer_id: int | None,
    statuses: Sequence[SaleStatus] | None,
    payment_type: PaymentType | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [QuickSale.shop_id == shop_id]
    if q and q.strip():
        needle = q.strip().lower()
        clauses = [
            func.lower(func.coalesce(QuickSale.quick_no, "")).contains(needle, autoescape=True),
            func.lower(func.coalesce(Customer.name, "")).contains(needle, autoescape=True),
            func.lower(func.coalesce(QuickSale.note, "")).contains(needle, autoescape=True),
        ]
        digits = "".join(ch for ch in needle if ch.isdigit() or ch == "+")
        if len(digits) >= 3:
            clauses.append(func.coalesce(Customer.phone, "").contains(digits, autoescape=True))
        conditions.append(or_(*clauses))
    if customer_id is not None:
        conditions.append(QuickSale.customer_id == customer_id)
    if statuses:
        conditions.append(QuickSale.status.in_(statuses))
    if payment_type is not None:
        conditions.append(QuickSale.payment_type == payment_type)
    if date_from is not None:
        conditions.append(QuickSale.sale_date >= date_from)
    if date_to is not None:
        conditions.append(QuickSale.sale_date <= date_to)
    return conditions


def list_quick_sales(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    customer_id: int | None = None,
    statuses: Sequence[SaleStatus] | None = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[QuickSaleRow], int]:
    """Quick sales, newest first. `limit=None` returns everything (used by exports)."""
    if date_from and date_to and date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    conditions = _filters(
        shop_id,
        q=q,
        customer_id=customer_id,
        statuses=statuses,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    join = (Customer.shop_id == QuickSale.shop_id) & (Customer.id == QuickSale.customer_id)
    total = session.scalar(
        select(func.count()).select_from(QuickSale).outerjoin(Customer, join).where(*conditions)
    )
    query = (
        select(QuickSale, Customer.name, User.full_name)
        .outerjoin(Customer, join)
        .join(User, (User.shop_id == QuickSale.shop_id) & (User.id == QuickSale.created_by))
        .where(*conditions)
        .order_by(QuickSale.sale_date.desc(), QuickSale.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [QuickSaleRow(*row) for row in session.execute(query)], total


# --- Validation ----------------------------------------------------------------------------------


def _positive(value: Any, *, field_name: str) -> Decimal:
    amount = payment_service.money(value, field_name=field_name)
    if amount <= 0:
        raise InvalidInputError("The amount must be greater than zero.", field=field_name)
    return amount


def _check_customer(session: Session, shop_id: int, customer_id: int | None) -> None:
    if customer_id is None:
        return
    found = select(Customer.id).where(Customer.shop_id == shop_id, Customer.id == customer_id)
    if session.scalar(found) is None:
        raise InvalidInputError("Customer not found.", field="customer_id")


def _clean(session: Session, shop_id: int, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Validate the fields present in `values` (all of them unless `partial`), reporting every problem."""
    out: dict[str, Any] = {}
    problems: list[tuple[str | None, str]] = []
    today = shop_today(get_shop(session, shop_id))

    def take(key: str, make: Any) -> None:
        """Run `make()`; keep its result under `key`, or remember what was wrong with it."""
        try:
            out[key] = make()
        except InvalidInputError as exc:
            problems.extend(exc.errors)

    if not partial or "sale_date" in values:
        when = values.get("sale_date") or today
        if when > today:
            problems.append(("sale_date", "The date cannot be in the future."))
        out["sale_date"] = when
    if not partial or "gross_amount" in values:
        take("gross_amount", lambda: _positive(values.get("gross_amount"), field_name="gross_amount"))
    if "discount" in values:
        given = values["discount"]
        take(
            "discount", lambda: payment_service.money(ZERO if given is None else given, field_name="discount")
        )
    if "customer_id" in values:
        take(
            "customer_id",
            lambda: _check_customer(session, shop_id, values["customer_id"]) or values["customer_id"],
        )
    if "note" in values:
        text = (values["note"] or "").strip()
        out["note"] = text or None
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


def _snapshot(sale: QuickSale) -> dict[str, Any]:
    return {
        "status": sale.status,
        "quick_no": sale.quick_no,
        "customer_id": sale.customer_id,
        "sale_date": sale.sale_date,
        "gross_amount": sale.gross_amount,
        "discount": sale.discount,
        "total_amount": sale.total_amount,
        "amount_paid": sale.amount_paid,
        "payment_type": sale.payment_type,
        "payment_method": sale.payment_method,
        "note": sale.note,
    }


def _apply_amounts(sale: QuickSale, gross: Decimal, discount: Decimal) -> None:
    """Gross, discount and total change together (the database requires total = gross - discount)."""
    if discount >= gross:
        raise InvalidInputError("The discount must be less than the amount.", field="discount")
    sale.gross_amount, sale.discount, sale.total_amount = gross, discount, gross - discount


# --- Writing -------------------------------------------------------------------------------------


def create_quick_sale(session: Session, ctx: RequestContext, values: dict[str, Any]) -> QuickSaleView:
    """Start a quick sale entry (a draft). Nothing is posted and nothing else changes."""
    clean = _clean(session, ctx.shop_id, values, partial=False)
    discount = clean.get("discount", ZERO)
    sale = QuickSale(
        shop_id=ctx.shop_id,
        status=SaleStatus.DRAFT,
        created_by=ctx.user_id,
        sale_date=clean["sale_date"],
        customer_id=clean.get("customer_id"),
        note=clean.get("note"),
        gross_amount=clean["gross_amount"],
        discount=ZERO,
        total_amount=clean["gross_amount"],
    )
    _apply_amounts(sale, clean["gross_amount"], discount)
    session.add(sale)
    session.flush()
    record_audit(
        session, ctx, entity_type="quick_sale", entity_id=sale.id, action="create", after=_snapshot(sale)
    )
    return get_quick_sale_view(session, ctx.shop_id, sale.id)


def update_quick_sale(
    session: Session, ctx: RequestContext, quick_sale_id: int, changes: dict[str, Any]
) -> QuickSaleView:
    """Change a draft (amount, discount, date, customer, note). A posted or void entry cannot be edited."""
    sale = _get(session, ctx.shop_id, quick_sale_id, lock=True)
    if sale.status is SaleStatus.POSTED:
        raise ConflictError(
            "This quick sale has been posted and can no longer be edited. Void it and enter a new one."
        )
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This quick sale is void and cannot be edited.")
    clean = _clean(session, ctx.shop_id, changes, partial=True)
    before = _snapshot(sale)
    for key in ("sale_date", "customer_id", "note"):
        if key in clean:
            setattr(sale, key, clean[key])
    _apply_amounts(sale, clean.get("gross_amount", sale.gross_amount), clean.get("discount", sale.discount))
    session.flush()
    record_audit(
        session, ctx, entity_type="quick_sale", entity_id=sale.id, action="update",
        before=before, after=_snapshot(sale),
    )  # fmt: skip
    return get_quick_sale_view(session, ctx.shop_id, sale.id)


def post_quick_sale(
    session: Session,
    ctx: RequestContext,
    quick_sale_id: int,
    *,
    amount_paid: Decimal | None = None,
    payment_method: PaymentMethod | None = None,
    payment_reference: str | None = None,
) -> QuickSaleView:
    """Post a draft: settle the payment, number it, charge any unpaid part to the khata. All or nothing.

    `amount_paid=None` means paid in full. The row is locked, so two requests posting the same draft cannot
    both succeed. Nothing here touches stock or cost: there is nothing to touch.
    """
    sale = _get(session, ctx.shop_id, quick_sale_id, lock=True)
    if sale.status is SaleStatus.POSTED:
        raise ConflictError(f"This quick sale has already been posted ({sale.quick_no}).")
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This quick sale is void and cannot be posted.")
    today = shop_today(get_shop(session, ctx.shop_id))
    if sale.sale_date > today:
        raise InvalidInputError("The date cannot be in the future.", field="sale_date")

    payment = payment_service.resolve_payment(
        sale.total_amount,
        sale.customer_id,
        amount_paid,
        payment_method,
        payment_reference,
        total_field="gross_amount",
    )
    before = _snapshot(sale)
    entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_INVOICES)

    fiscal_year = numbering_service.fiscal_year_label(today)
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, fiscal_year)
    sale.quick_no = numbering_service.format_document_number(NUMBER_PREFIX, fiscal_year, number)
    sale.payment_type = payment.split.payment_type
    sale.amount_paid = payment.split.paid
    sale.payment_method = payment.method
    sale.payment_reference = payment.reference
    sale.status = SaleStatus.POSTED
    sale.posted_at = utc_now()
    sale.posted_by = ctx.user_id
    session.flush()

    if payment.split.credit > 0:
        khata_service.record_credit_sale(
            session,
            ctx,
            sale.customer_id,
            payment.split.credit,
            reference_type=KhataReferenceType.QUICK_SALE,
            reference_id=sale.id,
            entry_date=sale.sale_date,
            note=f"Quick sale {sale.quick_no}",
        )
    loyalty_service.earn_for_sale(
        session, ctx, customer_id=sale.customer_id, amount=sale.gross_amount,
        reference_type="QUICK_SALE", reference_id=sale.id, entry_date=sale.sale_date,
    )  # fmt: skip
    referral_service.qualify_from_sale(
        session, ctx, customer_id=sale.customer_id, amount=sale.gross_amount,
        reference_type="QUICK_SALE", reference_id=sale.id, entry_date=sale.sale_date,
    )  # fmt: skip
    record_audit(
        session, ctx, entity_type="quick_sale", entity_id=sale.id, action="post",
        before=before, after=_snapshot(sale),
    )  # fmt: skip
    return get_quick_sale_view(session, ctx.shop_id, sale.id)


def void_quick_sale(session: Session, ctx: RequestContext, quick_sale_id: int, reason: str) -> QuickSaleView:
    """Cancel an entry. A posted one has its khata charge reversed; a draft is simply discarded."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise InvalidInputError("Give a reason for voiding this quick sale.", field="reason")
    if len(cleaned) > 500:
        raise InvalidInputError("The reason is too long (500 characters at most).", field="reason")
    sale = _get(session, ctx.shop_id, quick_sale_id, lock=True)
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This quick sale is already void.")

    before = _snapshot(sale)
    was_posted = sale.status is SaleStatus.POSTED
    if was_posted:
        void_reason = f"Void of {sale.quick_no}: {cleaned}"
        khata_service.reverse_credit_sale(
            session, ctx, KhataReferenceType.QUICK_SALE, sale.id, reason=void_reason
        )
        loyalty_service.reverse_for_sale(
            session, ctx, reference_type="QUICK_SALE", reference_id=sale.id, entry_date=sale.sale_date,
            reason=void_reason,
        )  # fmt: skip
    sale.status = SaleStatus.VOID
    sale.void_reason = cleaned
    sale.voided_at = utc_now()
    session.flush()
    record_audit(
        session, ctx, entity_type="quick_sale", entity_id=sale.id, action="void" if was_posted else "discard",
        before=before, after={**_snapshot(sale), "void_reason": cleaned},
    )  # fmt: skip
    return get_quick_sale_view(session, ctx.shop_id, sale.id)
