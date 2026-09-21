"""Sales returns: goods a customer brings back from a completed sale.

A return refers to the original sale LINES (BUSINESS_RULES R1): the total returned per line can never
exceed what was sold. Posting is one transaction: the sale row is locked first (so two returns cannot both
take the last of a line), the goods go back on the shelf through `inventory_service` at the cost of the
line they were sold from, the refund is worked out by `return_calculation`, and if the refund is credited
to the customer's khata that goes through `khata_service`. If anything fails nothing is kept. A return is
never edited or deleted; a wrong one is voided, which takes the goods off the shelf again and takes the
credit back off the khata.

Refund modes: CASH and UPI give money back, so together they can never be more than the money actually
received for the sale; KHATA reduces what the customer owes (only for a sale that has a customer). A sale
on credit whose money was never received can therefore only be refunded through the khata. Voiding a sale
is refused while it has live returns (the sale service checks).

Generic for every kind of business. Every query is scoped to the caller's shop.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import round_money, utc_now
from app.models import Customer, Product, Sale, SaleItem, SalesReturn, SalesReturnItem, Unit, User
from app.models.enums import DocumentStatus, KhataReferenceType, RefundMode, SaleStatus, StockReferenceType
from app.services import inventory_service, khata_service, numbering_service
from app.services import return_calculation as calc
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DOC_TYPE = "SALES_RETURN"
NUMBER_PREFIX = "SRT"
MAX_ITEMS = 200
ZERO = calc.ZERO

ItemInput = dict[str, Any]  # sale_item_id, quantity


@dataclass(frozen=True)
class _Basis:
    """What one sale line came to and how much of it has already come back (live returns only)."""

    item: SaleItem
    product: Product
    unit: Unit
    net: Decimal
    returned_qty: Decimal
    refunded: Decimal

    @property
    def returnable(self) -> Decimal:
        return self.item.quantity - self.returned_qty


@dataclass(frozen=True)
class PreviewLine:
    sale_item_id: int | None
    product_name: str | None
    sku: str | None
    unit_code: str | None
    sold: Decimal | None
    already_returned: Decimal | None
    returnable: Decimal | None
    quantity: Decimal | None
    refund: Decimal | None
    errors: list[tuple[str, str]]


@dataclass(frozen=True)
class Preview:
    sale_id: int
    lines: list[PreviewLine]
    total_refund: Decimal
    cash_refundable: Decimal  # the most that can be given back as cash or UPI for this sale
    khata_allowed: bool
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ReturnLineView:
    item: SalesReturnItem
    product_name: str
    sku: str
    unit_code: str
    sold_quantity: Decimal


@dataclass(frozen=True)
class SalesReturnView:
    ret: SalesReturn
    invoice_no: str
    customer_id: int | None
    customer_name: str | None
    created_by_name: str
    items: list[ReturnLineView]

    @property
    def cogs_total(self) -> Decimal | None:
        costs = [i.item.cogs_amount for i in self.items]
        return (
            None
            if not costs or any(c is None for c in costs)
            else sum((c for c in costs if c is not None), ZERO)
        )


@dataclass(frozen=True)
class ReturnRow:
    ret: SalesReturn
    invoice_no: str
    customer_name: str | None
    item_count: int


# --- Reading -------------------------------------------------------------------------------------


def _get_sale(session: Session, shop_id: int, sale_id: int, *, lock: bool = False) -> Sale:
    query = select(Sale).where(Sale.shop_id == shop_id, Sale.id == sale_id)
    sale = session.scalar(query.with_for_update() if lock else query)
    if sale is None:
        raise NotFoundError("Sale not found")  # also the answer for another shop's sale
    return sale


def _live_returns(shop_id: int, sale_id: int | None = None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        SalesReturn.shop_id == shop_id,
        SalesReturn.status == DocumentStatus.POSTED,
    ]
    if sale_id is not None:
        conditions.append(SalesReturn.sale_id == sale_id)
    return conditions


def _bases(session: Session, shop_id: int, sale: Sale) -> dict[int, _Basis]:
    rows = session.execute(
        select(SaleItem, Product, Unit)
        .join(Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id))
        .join(Unit, Unit.id == SaleItem.unit_id)
        .where(SaleItem.shop_id == shop_id, SaleItem.sale_id == sale.id)
        .order_by(SaleItem.id)
    ).all()
    nets = calc.line_nets(
        [r[0].line_total for r in rows], [r[0].promotion_discount for r in rows], sale.discount
    )
    returned = {
        item_id: (qty, refund)
        for item_id, qty, refund in session.execute(
            select(
                SalesReturnItem.sale_item_id,
                func.sum(SalesReturnItem.quantity),
                func.sum(SalesReturnItem.refund_amount),
            )
            .join(
                SalesReturn,
                (SalesReturn.shop_id == SalesReturnItem.shop_id)
                & (SalesReturn.id == SalesReturnItem.sales_return_id),
            )
            .where(*_live_returns(shop_id, sale.id))
            .group_by(SalesReturnItem.sale_item_id)
        )
    }
    out: dict[int, _Basis] = {}
    for (item, product, unit), net in zip(rows, nets, strict=True):
        qty, refund = returned.get(item.id, (ZERO, ZERO))
        out[item.id] = _Basis(item, product, unit, net, qty or ZERO, refund or ZERO)
    return out


def cash_refundable(session: Session, shop_id: int, sale: Sale) -> Decimal:
    """Money actually received for the sale, less what has already been given back in cash or UPI."""
    given = session.scalar(
        select(func.coalesce(func.sum(SalesReturn.total_refund), 0)).where(
            *_live_returns(shop_id, sale.id), SalesReturn.refund_mode.in_([RefundMode.CASH, RefundMode.UPI])
        )
    )
    paid = sale.amount_paid or ZERO
    remaining = paid - (given if isinstance(given, Decimal) else ZERO)
    return max(remaining, ZERO)


def _require_completed(sale: Sale) -> None:
    if sale.status is not SaleStatus.POSTED:
        raise ConflictError("Only a completed sale can have items returned.")


def _clean_lines(
    bases: dict[int, _Basis], raw_items: Sequence[ItemInput]
) -> tuple[list[tuple[_Basis, Decimal, Decimal]], list[PreviewLine], list[tuple[str | None, str]]]:
    """Validate the lines against what was sold and what is already back. Returns the good lines with their
    refund, a preview row for every raw line, and every problem found."""
    problems: list[tuple[str | None, str]] = []
    preview: list[PreviewLine] = []
    good: list[tuple[_Basis, Decimal, Decimal]] = []
    seen: set[int] = set()
    for index, raw in enumerate(raw_items[:MAX_ITEMS]):
        path = f"items.{index}"
        found: list[tuple[str, str]] = []
        item_id, quantity = raw.get("sale_item_id"), raw.get("quantity")
        basis = bases.get(item_id) if item_id is not None else None
        if basis is None:
            found.append((f"{path}.sale_item_id", "This line is not part of the sale."))
        elif item_id in seen:
            found.append((f"{path}.sale_item_id", "This line is listed twice."))
        if quantity is None or quantity <= 0:
            found.append((f"{path}.quantity", "Quantity must be greater than zero."))
        refund: Decimal | None = None
        if basis is not None:
            seen.add(basis.item.id)
            if quantity is not None and quantity > 0:
                try:
                    inventory_service.validate_quantity_for_unit(
                        quantity, basis.unit, field=f"{path}.quantity"
                    )
                except InvalidInputError as exc:
                    found.extend((f or "", m) for f, m in exc.errors)
                if quantity > basis.returnable:
                    left = f"{basis.returnable.normalize():f} {basis.unit.code}"
                    sold = f"{basis.item.quantity.normalize():f} sold"
                    back = f"{basis.returned_qty.normalize():f} already returned"
                    message = f"Only {left} of '{basis.product.name}' can still be returned ({sold}, {back})."
                    found.append((f"{path}.quantity", message))
                elif not found:
                    refund = calc.refund_for(
                        basis.net, basis.item.quantity, basis.returned_qty, basis.refunded, quantity
                    )
                    good.append((basis, quantity, refund))
        problems.extend(found)
        preview.append(
            PreviewLine(
                sale_item_id=item_id,
                product_name=None if basis is None else basis.product.name,
                sku=None if basis is None else basis.product.sku,
                unit_code=None if basis is None else basis.unit.code,
                sold=None if basis is None else basis.item.quantity,
                already_returned=None if basis is None else basis.returned_qty,
                returnable=None if basis is None else basis.returnable,
                quantity=quantity,
                refund=refund,
                errors=[(p.rsplit(".", 1)[-1], m) for p, m in found],
            )
        )
    return good, preview, problems


def calculate_preview(
    session: Session,
    shop_id: int,
    sale_id: int,
    raw_items: Sequence[ItemInput],
    refund_mode: RefundMode | None = None,
) -> Preview:
    """Price a return without saving anything: what would come back and be refunded."""
    sale = _get_sale(session, shop_id, sale_id)
    _require_completed(sale)
    bases = _bases(session, shop_id, sale)
    good, preview, _ = _clean_lines(bases, raw_items)
    total = sum((refund for _, _, refund in good), ZERO)
    limit = cash_refundable(session, shop_id, sale)
    errors: list[tuple[str, str]] = []
    if refund_mode in (RefundMode.CASH, RefundMode.UPI) and total > limit:
        errors.append(("refund_mode", _cash_message(limit)))
    if refund_mode is RefundMode.KHATA and sale.customer_id is None:
        errors.append(("refund_mode", "This sale has no customer, so it cannot be credited to a khata."))
    return Preview(sale.id, preview, total, limit, sale.customer_id is not None, errors)


def _cash_message(limit: Decimal) -> str:
    return (
        f"Only {limit:.2f} was received for this sale and not yet given back, so at most that can be "
        "refunded in cash or UPI. Credit the rest to the customer's khata instead."
    )


def get_view(session: Session, shop_id: int, return_id: int) -> SalesReturnView:
    row = session.execute(
        select(SalesReturn, Sale.invoice_no, Sale.customer_id, Customer.name, User.full_name)
        .join(Sale, (Sale.shop_id == SalesReturn.shop_id) & (Sale.id == SalesReturn.sale_id))
        .outerjoin(Customer, (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id))
        .join(User, (User.shop_id == SalesReturn.shop_id) & (User.id == SalesReturn.created_by))
        .where(SalesReturn.shop_id == shop_id, SalesReturn.id == return_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise NotFoundError("Return not found")
    ret, invoice_no, customer_id, customer_name, created_by = row
    lines = session.execute(
        select(SalesReturnItem, Product.name, Product.sku, Unit.code, SaleItem.quantity)
        .join(
            SaleItem,
            (SaleItem.shop_id == SalesReturnItem.shop_id) & (SaleItem.id == SalesReturnItem.sale_item_id),
        )
        .join(
            Product, (Product.shop_id == SalesReturnItem.shop_id) & (Product.id == SalesReturnItem.product_id)
        )
        .join(Unit, Unit.id == SaleItem.unit_id)
        .where(SalesReturnItem.shop_id == shop_id, SalesReturnItem.sales_return_id == return_id)
        .order_by(SalesReturnItem.id)
        .execution_options(populate_existing=True)
    ).all()
    items = [ReturnLineView(*line) for line in lines]
    return SalesReturnView(ret, invoice_no, customer_id, customer_name, created_by, items)


def list_returns(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    sale_id: int | None = None,
    statuses: Sequence[DocumentStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[ReturnRow], int]:
    if date_from and date_to and date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    conditions: list[ColumnElement[bool]] = [SalesReturn.shop_id == shop_id]
    if q and q.strip():
        needle = q.strip().lower()
        conditions.append(
            or_(
                func.lower(SalesReturn.return_no).contains(needle, autoescape=True),
                func.lower(func.coalesce(Sale.invoice_no, "")).contains(needle, autoescape=True),
                func.lower(func.coalesce(Customer.name, "")).contains(needle, autoescape=True),
            )
        )
    if sale_id is not None:
        conditions.append(SalesReturn.sale_id == sale_id)
    if statuses:
        conditions.append(SalesReturn.status.in_(statuses))
    if date_from is not None:
        conditions.append(SalesReturn.return_date >= date_from)
    if date_to is not None:
        conditions.append(SalesReturn.return_date <= date_to)
    base = (
        select(SalesReturn)
        .join(Sale, (Sale.shop_id == SalesReturn.shop_id) & (Sale.id == SalesReturn.sale_id))
        .outerjoin(Customer, (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id))
        .where(*conditions)
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    item_count = (
        select(func.count())
        .where(
            SalesReturnItem.shop_id == SalesReturn.shop_id, SalesReturnItem.sales_return_id == SalesReturn.id
        )
        .correlate(SalesReturn)
        .scalar_subquery()
    )
    query = (
        select(SalesReturn, Sale.invoice_no, Customer.name, item_count)
        .join(Sale, (Sale.shop_id == SalesReturn.shop_id) & (Sale.id == SalesReturn.sale_id))
        .outerjoin(Customer, (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id))
        .where(*conditions)
        .order_by(SalesReturn.return_date.desc(), SalesReturn.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [ReturnRow(*row) for row in session.execute(query)], total


# --- Writing -------------------------------------------------------------------------------------


def _snapshot(ret: SalesReturn) -> dict[str, Any]:
    return {
        "return_no": ret.return_no,
        "sale_id": ret.sale_id,
        "status": ret.status,
        "return_date": ret.return_date,
        "refund_mode": ret.refund_mode,
        "total_refund": ret.total_refund,
        "reason": ret.reason,
    }


def create_sales_return(
    session: Session,
    ctx: RequestContext,
    sale_id: int,
    *,
    items: Sequence[ItemInput],
    refund_mode: RefundMode,
    return_date: date | None = None,
    reason: str | None = None,
) -> SalesReturnView:
    """Post a return: goods back on the shelf, refund settled, all or nothing."""
    sale = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    _require_completed(sale)
    shop = get_shop(session, ctx.shop_id)
    today = shop_today(shop)
    problems: list[tuple[str | None, str]] = []
    if not items:
        problems.append(("items", "Choose at least one item to return."))
    if len(items) > MAX_ITEMS:
        problems.append(("items", f"A return can have at most {MAX_ITEMS} items."))
    when = return_date or today
    if when > today:
        problems.append(("return_date", "The return date cannot be in the future."))
    elif when < sale.sale_date:
        problems.append(("return_date", "The return date cannot be before the sale."))
    cleaned_reason = (reason or "").strip() or None
    if cleaned_reason and len(cleaned_reason) > 500:
        problems.append(("reason", "The reason is too long (500 characters at most)."))

    bases = _bases(session, ctx.shop_id, sale)
    good, _, line_problems = _clean_lines(bases, items)
    problems.extend(line_problems)
    total = sum((refund for _, _, refund in good), ZERO)
    if refund_mode is RefundMode.KHATA and sale.customer_id is None:
        problems.append(("refund_mode", "This sale has no customer, so it cannot be credited to a khata."))
    if refund_mode in (RefundMode.CASH, RefundMode.UPI):
        limit = cash_refundable(session, ctx.shop_id, sale)
        if total > limit:
            problems.append(("refund_mode", _cash_message(limit)))
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)

    products = inventory_service.lock_products(session, ctx.shop_id, [b.product.id for b, _, _ in good])
    fiscal_year = numbering_service.fiscal_year_label(today)
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, fiscal_year)
    ret = SalesReturn(
        shop_id=ctx.shop_id,
        return_no=numbering_service.format_document_number(NUMBER_PREFIX, fiscal_year, number),
        sale_id=sale.id,
        return_date=when,
        refund_mode=refund_mode,
        total_refund=total,
        reason=cleaned_reason,
        status=DocumentStatus.POSTED,
        created_by=ctx.user_id,
    )
    session.add(ret)
    session.flush()
    for basis, quantity, refund in good:
        unit_cost = basis.item.unit_cost
        line = SalesReturnItem(
            shop_id=ctx.shop_id,
            sales_return_id=ret.id,
            sale_item_id=basis.item.id,
            product_id=basis.product.id,
            quantity=quantity,
            refund_amount=refund,
            unit_cost=unit_cost,
            cogs_amount=None if unit_cost is None else round_money(quantity * unit_cost),
        )
        session.add(line)
        session.flush()
        inventory_service.receive_sale_return_line(
            session,
            ctx,
            product=products[basis.product.id],
            quantity=quantity,
            sales_return_item_id=line.id,
            unit_cost=unit_cost,
            txn_date=when,
        )
    if refund_mode is RefundMode.KHATA and total > 0 and sale.customer_id is not None:
        khata_service.record_return_credit(
            session,
            ctx,
            sale.customer_id,
            total,
            reference_type=KhataReferenceType.SALES_RETURN,
            reference_id=ret.id,
            entry_date=when,
            note=f"Return {ret.return_no} of {sale.invoice_no}",
        )
    record_audit(
        session, ctx, entity_type="sales_return", entity_id=ret.id, action="post", after=_snapshot(ret)
    )
    return get_view(session, ctx.shop_id, ret.id)


def void_sales_return(session: Session, ctx: RequestContext, return_id: int, reason: str) -> SalesReturnView:
    """Cancel a return: the goods leave the shelf again and any khata credit is taken back. The return and
    its
    number stay on record."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise InvalidInputError("Give a reason for voiding this return.", field="reason")
    if len(cleaned) > 500:
        raise InvalidInputError("The reason is too long (500 characters at most).", field="reason")
    ret = session.scalar(
        select(SalesReturn)
        .where(SalesReturn.shop_id == ctx.shop_id, SalesReturn.id == return_id)
        .with_for_update()
    )
    if ret is None:
        raise NotFoundError("Return not found")
    if ret.status is DocumentStatus.VOID:
        raise ConflictError("This return is already void.")
    before = _snapshot(ret)
    item_ids = list(
        session.scalars(
            select(SalesReturnItem.id).where(
                SalesReturnItem.shop_id == ctx.shop_id, SalesReturnItem.sales_return_id == ret.id
            )
        )
    )
    note = f"Void of {ret.return_no}: {cleaned}"
    inventory_service.reverse_lines(
        session, ctx, reference_type=StockReferenceType.SALES_RETURN_ITEM, reference_ids=item_ids, note=note
    )
    khata_service.reverse_return_credit(session, ctx, ret.id, reason=note)
    ret.status = DocumentStatus.VOID
    ret.void_reason = cleaned
    ret.voided_at = utc_now()
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="sales_return",
        entity_id=ret.id,
        action="void",
        before=before,
        after={**_snapshot(ret), "void_reason": cleaned},
    )
    return get_view(session, ctx.shop_id, ret.id)
