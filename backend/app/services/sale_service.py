"""Detailed Sales: a product-wise bill, from a cart to stock leaving the shelf.

Generic for every kind of business: nothing here depends on the business type.

Lifecycle (`Sale.status`): DRAFT -> POSTED -> VOID (docs/BUSINESS_RULES.md, SL).
  * A DRAFT is a cart: customer, date, notes, lines and an optional bill discount. It has no number and no
    payment, and it never touches stock, khata, revenue or cost. It can be edited freely.
  * POSTING is the one moment things happen, all in a single database transaction: the payment is settled, the
    sale gets its number (INV/2026-27/0001), each line takes its quantity out of stock through
    `inventory_service` (refused if the shelf is short) and records the product's cost at that moment, and any
    part not paid is charged to the customer's khata through `khata_service`. If anything fails, nothing is
    kept. A sale can be posted only once.
  * A POSTED sale is never edited or deleted. VOIDING it reverses the stock (reversal rows) and the khata
    charge, and is refused while the sale has live returns (Phase 9). Voiding a draft just discards it.
  * To fix a posted sale: void it, then "correct" it, which copies it into a new draft to edit and post.

Money and cost: all arithmetic is in `sale_calculation`. The cost of goods sold of a line is its quantity
times the product's average cost when the sale is posted. An unknown cost stays unknown (never 0), and then
so does the profit. The bill discount reduces the sale's profit but is not spread over the lines.

Payment: chosen when posting. Paid in full needs a payment method (cash, UPI or other). Paying less leaves a
credit sale: a customer is required and the unpaid part goes on their khata. Paying more than the bill is
refused: extra money is an advance, recorded as a payment on the customer's khata.

Every query is scoped to the caller's shop; another shop's sale, customer or product is "not found".
Sales returns and quick sales are later phases.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Customer, Product, Sale, SaleItem, SalesReturn, Unit, User
from app.models.enums import (
    DocumentStatus,
    KhataReferenceType,
    MrpValidationMode,
    PaymentMethod,
    PaymentType,
    SaleStatus,
    StockReferenceType,
)
from app.services import inventory_service, khata_service, numbering_service
from app.services import sale_calculation as calc
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DOC_TYPE = "SALE"
NUMBER_PREFIX = "INV"
MAX_ITEMS = 200
ZERO = calc.ZERO
CENT = Decimal("0.01")

ItemInput = dict[
    str, Any
]  # product_id, quantity, and optionally unit_price (default: the product's price), discount
HEADER_FIELDS = ("sale_date", "customer_id", "notes", "discount")


@dataclass(frozen=True)
class SaleItemView:
    item: SaleItem
    product_sku: str
    product_name: str
    unit_code: str
    unit_name: str
    unit_allows_decimal: bool
    effects: list[inventory_service.LedgerEntry] = field(default_factory=list)  # ledger rows behind this line

    @property
    def profit(self) -> Decimal | None:
        return calc.line_profit(self.item.line_total, self.item.cogs_amount)


@dataclass(frozen=True)
class SaleView:
    sale: Sale
    customer_name: str | None
    created_by_name: str
    posted_by_name: str | None
    items: list[SaleItemView]
    replaced_by_id: int | None  # the corrected copy of this (voided) sale, if one was made
    warnings: list[str]  # for example a price above the MRP, when the shop only warns

    @property
    def credit_amount(self) -> Decimal:
        """The part of a posted sale that is on the customer's khata."""
        sale = self.sale
        if sale.amount_paid is None or sale.total_amount <= sale.amount_paid:
            return ZERO
        return sale.total_amount - sale.amount_paid

    @property
    def cogs_total(self) -> Decimal | None:
        return calc.total_cogs([i.item.cogs_amount for i in self.items]) if self._costed else None

    @property
    def gross_profit(self) -> Decimal | None:
        if not self._costed:
            return None
        return calc.gross_profit(self.sale.total_amount, [i.item.cogs_amount for i in self.items])

    @property
    def lines_without_cost(self) -> int:
        return sum(1 for i in self.items if i.item.cogs_amount is None) if self._costed else 0

    @property
    def _costed(self) -> bool:
        """Cost snapshots exist only once a sale has been posted."""
        return self.sale.status is not SaleStatus.DRAFT and self.sale.invoice_no is not None


@dataclass(frozen=True)
class SaleRow:
    sale: Sale
    customer_name: str | None
    created_by_name: str
    item_count: int
    cogs_sum: Decimal | None  # sum of the lines' known costs (None when no line has one)
    unknown_cost_lines: int

    @property
    def _costed(self) -> bool:
        return self.sale.invoice_no is not None and self.item_count > 0

    @property
    def cogs_total(self) -> Decimal | None:
        """Cost of goods sold, or None if the sale is unposted or any line's cost is unknown."""
        return self.cogs_sum if self._costed and self.unknown_cost_lines == 0 else None

    @property
    def gross_profit(self) -> Decimal | None:
        cogs = self.cogs_total
        return None if cogs is None else self.sale.total_amount - cogs


@dataclass(frozen=True)
class SaleItemRow:
    """One line with its sale, for the items export."""

    sale: Sale
    item: SaleItem
    customer_name: str | None
    product_sku: str
    product_name: str
    unit_code: str


@dataclass(frozen=True)
class PreviewLine:
    product_id: int | None
    quantity: Decimal | None
    unit_price: Decimal | None
    discount: Decimal
    gross: Decimal | None
    line_total: Decimal | None
    available: Decimal | None  # stock on hand now
    short: bool  # more requested (across all lines of that product) than is on hand
    errors: list[tuple[str, str]]  # (field, message) for this line


@dataclass(frozen=True)
class Preview:
    lines: list[PreviewLine]
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    payment_type: PaymentType  # PAID or CREDIT, for the amount paid that was asked about (default: in full)
    paid: Decimal
    credit: Decimal  # the part that would go on the customer's khata
    errors: list[tuple[str, str]]  # problems with the bill as a whole (path, message)
    warnings: list[str]


# --- Reading -------------------------------------------------------------------------------------


def _get_sale(session: Session, shop_id: int, sale_id: int, *, lock: bool = False) -> Sale:
    query = select(Sale).where(Sale.shop_id == shop_id, Sale.id == sale_id)
    sale = session.scalar(query.with_for_update() if lock else query)
    if sale is None:
        raise NotFoundError("Sale not found")  # also the answer for another shop's sale
    return sale


def _items_of(session: Session, shop_id: int, sale_id: int) -> list[SaleItem]:
    return list(
        session.scalars(
            select(SaleItem)
            .where(SaleItem.shop_id == shop_id, SaleItem.sale_id == sale_id)
            .order_by(SaleItem.id)
        )
    )


def _mrp_message(name: str, price: Decimal, mrp: Decimal) -> str:
    return f"'{name}' is priced at {price:.2f}, above its MRP of {mrp:.2f}."


def _mrp_warnings(items: Sequence[tuple[str, SaleItem]]) -> list[str]:
    """Lines priced above their MRP. In BLOCK mode such a line cannot be saved, so this only ever lists
    what a WARN shop allowed."""
    over = [
        _mrp_message(name, item.unit_price, item.mrp)
        for name, item in items
        if item.mrp is not None and item.unit_price > item.mrp
    ]
    return over


def get_sale_view(session: Session, shop_id: int, sale_id: int) -> SaleView:
    posted_by = aliased(User)
    row = session.execute(
        select(Sale, Customer.name, User.full_name, posted_by.full_name)
        .outerjoin(Customer, (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id))
        .join(User, (User.shop_id == Sale.shop_id) & (User.id == Sale.created_by))
        .outerjoin(posted_by, (posted_by.shop_id == Sale.shop_id) & (posted_by.id == Sale.posted_by))
        .where(Sale.shop_id == shop_id, Sale.id == sale_id)
        .execution_options(populate_existing=True)  # show what is stored, not the raw input
    ).first()
    if row is None:
        raise NotFoundError("Sale not found")
    sale, customer_name, created_by_name, posted_by_name = row

    lines = session.execute(
        select(SaleItem, Product.sku, Product.name, Unit.code, Unit.name, Unit.allows_decimal)
        .join(Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id))
        .join(Unit, Unit.id == SaleItem.unit_id)
        .where(SaleItem.shop_id == shop_id, SaleItem.sale_id == sale_id)
        .order_by(SaleItem.id)
        .execution_options(populate_existing=True)
    ).all()
    effects = inventory_service.entries_for_lines(
        session, shop_id, StockReferenceType.SALE_ITEM, [line[0].id for line in lines]
    )
    items = [
        SaleItemView(item, sku, name, unit_code, unit_name, allows_decimal, effects[item.id])
        for item, sku, name, unit_code, unit_name, allows_decimal in lines
    ]
    replaced_by_id = session.scalar(
        select(Sale.id).where(Sale.shop_id == shop_id, Sale.replaces_id == sale_id)
    )
    warnings = _mrp_warnings([(line[2], line[0]) for line in lines])
    return SaleView(sale, customer_name, created_by_name, posted_by_name, items, replaced_by_id, warnings)


def _search_clause(text: str) -> ColumnElement[bool]:
    """Match the invoice number, the customer's name or phone, or the notes."""
    needle = text.strip().lower()
    clauses = [
        func.lower(func.coalesce(Sale.invoice_no, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Customer.name, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Sale.notes, "")).contains(needle, autoescape=True),
    ]
    digits = "".join(ch for ch in needle if ch.isdigit() or ch == "+")
    if len(digits) >= 3:
        clauses.append(func.coalesce(Customer.phone, "").contains(digits, autoescape=True))
    return or_(*clauses)


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
    conditions: list[ColumnElement[bool]] = [Sale.shop_id == shop_id]
    if q and q.strip():
        conditions.append(_search_clause(q))
    if customer_id is not None:
        conditions.append(Sale.customer_id == customer_id)
    if statuses:
        conditions.append(Sale.status.in_(statuses))
    if payment_type is not None:
        conditions.append(Sale.payment_type == payment_type)
    if date_from is not None:
        conditions.append(Sale.sale_date >= date_from)
    if date_to is not None:
        conditions.append(Sale.sale_date <= date_to)
    return conditions


def _customer_join() -> ColumnElement[bool]:
    return (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id)


def list_sales(
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
) -> tuple[list[SaleRow], int]:
    """Sales, newest first. `limit=None` returns everything (used by exports)."""
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
    total = session.scalar(
        select(func.count()).select_from(Sale).outerjoin(Customer, _customer_join()).where(*conditions)
    )
    item_count = (
        select(func.count())
        .where(SaleItem.shop_id == Sale.shop_id, SaleItem.sale_id == Sale.id)
        .correlate(Sale)
        .scalar_subquery()
    )
    cogs_sum = (
        select(func.sum(SaleItem.cogs_amount))
        .where(SaleItem.shop_id == Sale.shop_id, SaleItem.sale_id == Sale.id)
        .correlate(Sale)
        .scalar_subquery()
    )
    unknown_cost_lines = (
        select(func.count())
        .where(SaleItem.shop_id == Sale.shop_id, SaleItem.sale_id == Sale.id, SaleItem.cogs_amount.is_(None))
        .correlate(Sale)
        .scalar_subquery()
    )
    query = (
        select(Sale, Customer.name, User.full_name, item_count, cogs_sum, unknown_cost_lines)
        .outerjoin(Customer, _customer_join())
        .join(User, (User.shop_id == Sale.shop_id) & (User.id == Sale.created_by))
        .where(*conditions)
        .order_by(Sale.sale_date.desc(), Sale.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [SaleRow(*row) for row in session.execute(query)], total


def list_item_rows(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    customer_id: int | None = None,
    statuses: Sequence[SaleStatus] | None = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sale_id: int | None = None,
) -> list[SaleItemRow]:
    """Every line of the matching sales (or of one sale), in sale order. For the items export."""
    conditions = _filters(
        shop_id,
        q=q,
        customer_id=customer_id,
        statuses=statuses,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    if sale_id is not None:
        conditions.append(Sale.id == sale_id)
    query = (
        select(Sale, SaleItem, Customer.name, Product.sku, Product.name, Unit.code)
        .outerjoin(Customer, _customer_join())
        .join(SaleItem, (SaleItem.shop_id == Sale.shop_id) & (SaleItem.sale_id == Sale.id))
        .join(Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id))
        .join(Unit, Unit.id == SaleItem.unit_id)
        .where(*conditions)
        .order_by(Sale.sale_date.desc(), Sale.id.desc(), SaleItem.id)
    )
    return [SaleItemRow(*row) for row in session.execute(query)]


# --- Validation ----------------------------------------------------------------------------------


def _path(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _money(value: Any, *, field_name: str) -> Decimal:
    """A non-negative amount with at most two decimals (the API already checks; services are used directly
    too)."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal | int):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field_name)
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise InvalidInputError("The amount cannot be negative.", field=field_name)
    if amount != amount.quantize(CENT):
        raise InvalidInputError("Use at most 2 decimal places.", field=field_name)
    return amount.quantize(CENT)


@dataclass(frozen=True)
class _Line:
    product: Product
    unit: Unit
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    gross: Decimal
    total: Decimal


def _check_customer(session: Session, shop_id: int, customer_id: int | None) -> None:
    if customer_id is None:
        return
    exists = session.scalar(
        select(Customer.id).where(Customer.shop_id == shop_id, Customer.id == customer_id)
    )
    if exists is None:
        raise InvalidInputError("Customer not found.", field="customer_id")


def _clean_item(
    session: Session, shop_id: int, raw: ItemInput, prefix: str, mode: MrpValidationMode
) -> tuple[_Line | None, list[tuple[str, str]]]:
    """Validate one line. Returns the cleaned line, or None plus every problem found."""
    problems: list[tuple[str, str]] = []
    product = None
    product_id = raw.get("product_id")
    if product_id is None:
        problems.append((_path(prefix, "product_id"), "Choose the product."))
    else:
        product = session.scalar(select(Product).where(Product.shop_id == shop_id, Product.id == product_id))
        if product is None:
            problems.append((_path(prefix, "product_id"), "Product not found."))
        elif not product.is_active:
            problems.append(
                (_path(prefix, "product_id"), f"'{product.name}' is inactive and cannot be sold.")
            )

    quantity = raw.get("quantity")
    if quantity is None or quantity <= 0:
        problems.append((_path(prefix, "quantity"), "Quantity must be greater than zero."))

    unit_price = raw.get("unit_price")
    if unit_price is None and product is not None:
        unit_price = product.selling_price  # the product's own price, unless the cashier typed another
    discount = raw.get("discount") or ZERO

    unit = None
    if product is not None:
        unit = session.scalars(select(Unit).where(Unit.id == product.unit_id)).one()
        if quantity is not None and quantity > 0:
            try:
                inventory_service.validate_quantity_for_unit(quantity, unit, field=_path(prefix, "quantity"))
            except InvalidInputError as exc:
                problems.extend((f or "", m) for f, m in exc.errors)
        if unit_price is not None and product.mrp is not None and unit_price > product.mrp:
            if mode is MrpValidationMode.BLOCK:
                problems.append(
                    (
                        _path(prefix, "unit_price"),
                        f"Price {unit_price:.2f} is above the MRP {product.mrp:.2f}. "
                        "This shop does not allow selling above MRP.",
                    )
                )

    gross = total = None
    if quantity is not None and quantity > 0 and unit_price is not None:
        gross = calc.line_gross(quantity, unit_price)
        try:
            total = calc.line_total(gross, discount)
        except calc.DiscountTooLargeError:
            problems.append((_path(prefix, "discount"), "The discount is more than the line amount."))

    if problems or product is None or unit is None or gross is None or total is None:
        return None, problems
    return _Line(product, unit, quantity, unit_price, discount, gross, total), []


def _clean_items(
    session: Session, shop_id: int, raw_items: Sequence[ItemInput], mode: MrpValidationMode
) -> list[_Line]:
    if len(raw_items) > MAX_ITEMS:
        raise InvalidInputError(f"A sale can have at most {MAX_ITEMS} items.", field="items")
    lines: list[_Line] = []
    problems: list[tuple[str | None, str]] = []
    for index, raw in enumerate(raw_items):
        line, found = _clean_item(session, shop_id, raw, f"items.{index}", mode)
        problems.extend(found)
        if line is not None:
            lines.append(line)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return lines


def _clean_header(session: Session, shop_id: int, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    problems: list[tuple[str | None, str]] = []
    today = shop_today(get_shop(session, shop_id))

    def guard(step: Any) -> None:
        try:
            step()
        except InvalidInputError as exc:
            problems.extend(exc.errors)

    if not partial or "sale_date" in values:
        when = values.get("sale_date") or today
        if when > today:
            problems.append(("sale_date", "The sale date cannot be in the future."))
        out["sale_date"] = when
    if "customer_id" in values:
        guard(lambda: _check_customer(session, shop_id, values["customer_id"]))
        out["customer_id"] = values["customer_id"]
    if "notes" in values:
        out["notes"] = _clean_text(values["notes"])
    if "discount" in values:
        try:
            out["discount"] = _money(
                values["discount"] if values["discount"] is not None else ZERO, field_name="discount"
            )
        except InvalidInputError as exc:
            problems.extend(exc.errors)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


# --- Preview -------------------------------------------------------------------------------------


def calculate_preview(
    session: Session,
    shop_id: int,
    raw_items: Sequence[ItemInput],
    discount: Decimal | None = None,
    amount_paid: Decimal | None = None,
) -> Preview:
    """Price a cart without saving anything: the numbers the billing screen shows.

    The screen never does this arithmetic itself, so what it shows is what posting will compute. Problems are
    returned per line instead of raised, so the whole cart can be marked at once. Stock is reported as
    `available` and `short`; a short cart is still a valid draft, it just cannot be posted.
    """
    shop = get_shop(session, shop_id)
    lines: list[PreviewLine] = []
    cleaned: list[_Line | None] = []
    for index, raw in enumerate(raw_items[:MAX_ITEMS]):
        line, problems = _clean_item(session, shop_id, raw, f"items.{index}", shop.mrp_validation_mode)
        cleaned.append(line)
        lines.append(
            PreviewLine(
                product_id=raw.get("product_id"),
                quantity=raw.get("quantity"),
                unit_price=None if line is None else line.unit_price,
                discount=raw.get("discount") or ZERO,
                gross=None if line is None else line.gross,
                line_total=None if line is None else line.total,
                available=None,
                short=False,
                errors=[(p.rsplit(".", 1)[-1], m) for p, m in problems],
            )
        )

    requested: dict[int, Decimal] = {}
    for line in cleaned:
        if line is not None:
            requested[line.product.id] = requested.get(line.product.id, ZERO) + line.quantity
    stock = inventory_service.get_stock_map(session, shop_id, list(requested))
    short_ids = {s.product_id for s in inventory_service.find_shortages(session, shop_id, requested)}
    lines = [
        PreviewLine(
            **{
                **pl.__dict__,
                "available": None if line is None else stock[line.product.id],
                "short": line is not None and line.product.id in short_ids,
            }
        )
        for pl, line in zip(lines, cleaned, strict=True)
    ]

    errors: list[tuple[str, str]] = []
    bill_discount = ZERO
    try:
        bill_discount = _money(discount if discount is not None else ZERO, field_name="discount")
    except InvalidInputError as exc:
        errors.append(("discount", exc.message))
    good_totals = [line.total for line in cleaned if line is not None]
    try:
        totals = calc.bill_totals(good_totals, bill_discount)
    except calc.DiscountTooLargeError:
        errors.append(("discount", "The bill discount is more than the items total."))
        totals = calc.bill_totals(good_totals)
    split = calc.split_payment(totals.total, None)
    if amount_paid is not None:
        try:
            paid = _money(amount_paid, field_name="amount_paid")
            if paid > totals.total:
                errors.append(("amount_paid", "The amount paid is more than the bill total."))
            else:
                split = calc.split_payment(totals.total, paid)
        except InvalidInputError as exc:
            errors.append(("amount_paid", exc.message))
    return Preview(
        lines=lines,
        subtotal=totals.subtotal,
        discount=totals.discount,
        total=totals.total,
        payment_type=split.payment_type,
        paid=split.paid,
        credit=split.credit,
        errors=errors,
        warnings=[
            _mrp_message(line.product.name, line.unit_price, line.product.mrp)
            for line in cleaned
            if line is not None and line.product.mrp is not None and line.unit_price > line.product.mrp
        ],
    )


# --- Writing: drafts -----------------------------------------------------------------------------


def _snapshot(sale: Sale, items: Sequence[SaleItem] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": sale.status,
        "invoice_no": sale.invoice_no,
        "customer_id": sale.customer_id,
        "sale_date": sale.sale_date,
        "subtotal": sale.subtotal,
        "discount": sale.discount,
        "total_amount": sale.total_amount,
        "amount_paid": sale.amount_paid,
        "payment_type": sale.payment_type,
        "payment_method": sale.payment_method,
        "notes": sale.notes,
    }
    if items is not None:
        data["items"] = [
            {
                "product_id": i.product_id,
                "quantity": i.quantity,
                "unit_price": i.unit_price,
                "discount": i.discount,
                "line_total": i.line_total,
                "unit_cost": i.unit_cost,
                "cogs_amount": i.cogs_amount,
            }
            for i in items
        ]
    return data


def _require_draft(sale: Sale) -> None:
    if sale.status is SaleStatus.POSTED:
        raise ConflictError(
            "This sale has been posted and can no longer be edited. "
            "Void it and make a corrected copy if something is wrong."
        )
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This sale is void and cannot be edited.")


def _write_items(session: Session, ctx: RequestContext, sale: Sale, lines: Sequence[_Line]) -> None:
    for line in lines:
        session.add(
            SaleItem(
                shop_id=ctx.shop_id,
                sale_id=sale.id,
                product_id=line.product.id,
                unit_id=line.unit.id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                mrp=line.product.mrp,  # snapshot (P3)
                discount=line.discount,
                line_total=line.total,
            )
        )
    session.flush()


def _retotal(session: Session, sale: Sale, *, discount: Decimal | None = None) -> None:
    """Set subtotal, discount and total together, from the lines (the database requires
    `total = subtotal - discount` at every flush, so they can only change as one)."""
    totals = [i.line_total for i in _items_of(session, sale.shop_id, sale.id)]  # read first: it autoflushes
    new_discount = sale.discount if discount is None else discount
    try:
        bill = calc.bill_totals(totals, new_discount)
    except calc.DiscountTooLargeError:
        raise InvalidInputError("The bill discount is more than the items total.", field="discount") from None
    sale.subtotal, sale.discount, sale.total_amount = bill.subtotal, bill.discount, bill.total
    session.flush()


def create_sale(
    session: Session,
    ctx: RequestContext,
    header: dict[str, Any],
    items: Sequence[ItemInput] | None = None,
) -> SaleView:
    """Create a draft (a cart), optionally with its lines. Nothing is posted and no stock moves."""
    shop = get_shop(session, ctx.shop_id)
    problems: list[tuple[str | None, str]] = []
    values: dict[str, Any] = {}
    lines: list[_Line] = []
    try:
        values = _clean_header(session, ctx.shop_id, header, partial=False)
    except InvalidInputError as exc:
        problems.extend(exc.errors)
    try:
        lines = _clean_items(session, ctx.shop_id, items or [], shop.mrp_validation_mode)
    except InvalidInputError as exc:
        problems.extend(exc.errors)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)

    sale = Sale(
        shop_id=ctx.shop_id,
        status=SaleStatus.DRAFT,
        created_by=ctx.user_id,
        subtotal=ZERO,
        discount=ZERO,
        total_amount=ZERO,
        **{key: values.get(key) for key in HEADER_FIELDS if key in values and key != "discount"},
    )
    session.add(sale)
    session.flush()
    _write_items(session, ctx, sale, lines)
    _retotal(session, sale, discount=values.get("discount"))
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=sale.id,
        action="create",
        after=_snapshot(sale, _items_of(session, ctx.shop_id, sale.id)),
    )
    return get_sale_view(session, ctx.shop_id, sale.id)


def update_sale(session: Session, ctx: RequestContext, sale_id: int, changes: dict[str, Any]) -> SaleView:
    """Change header fields of a draft (customer, date, notes, bill discount)."""
    sale = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    _require_draft(sale)
    values = _clean_header(session, ctx.shop_id, changes, partial=True)
    before = _snapshot(sale)
    for key, value in values.items():
        if key != "discount":
            setattr(sale, key, value)
    _retotal(session, sale, discount=values.get("discount"))
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=sale.id,
        action="update",
        before=before,
        after=_snapshot(sale),
    )
    return get_sale_view(session, ctx.shop_id, sale.id)


def replace_items(
    session: Session, ctx: RequestContext, sale_id: int, items: Sequence[ItemInput]
) -> SaleView:
    """Replace all lines of a draft with the given ones. This is how lines are edited or removed."""
    sale = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    _require_draft(sale)
    lines = _clean_items(session, ctx.shop_id, items, get_shop(session, ctx.shop_id).mrp_validation_mode)
    before_items = _items_of(session, ctx.shop_id, sale.id)
    before = _snapshot(sale, before_items)
    # A draft's lines have no ledger rows yet, so replacing them is safe (the ledger is never involved).
    for old in before_items:
        session.delete(old)
    session.flush()
    _write_items(session, ctx, sale, lines)
    _retotal(session, sale)
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=sale.id,
        action="items_changed",
        before=before,
        after=_snapshot(sale, _items_of(session, ctx.shop_id, sale.id)),
    )
    return get_sale_view(session, ctx.shop_id, sale.id)


# --- Writing: posting ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Payment:
    split: calc.PaymentSplit
    method: PaymentMethod | None
    reference: str | None


def _resolve_payment(
    total: Decimal,
    customer_id: int | None,
    amount_paid: Decimal | None,
    method: PaymentMethod | None,
    reference: str | None,
) -> _Payment:
    if total <= 0:
        raise InvalidInputError("The bill total must be greater than zero.", field="items")
    paid = None if amount_paid is None else _money(amount_paid, field_name="amount_paid")
    if paid is not None and paid > total:
        raise InvalidInputError(
            f"The amount paid is more than the bill total ({total:.2f}). To keep extra money as an advance, "
            "take a payment on the customer's khata instead.",
            field="amount_paid",
        )
    split = calc.split_payment(total, paid)
    if split.paid > 0 and method is None:
        raise InvalidInputError("Choose how the customer paid (cash, UPI or other).", field="payment_method")
    if split.credit > 0 and customer_id is None:
        raise InvalidInputError(
            "Choose the customer: paying less than the total leaves an amount on their khata.",
            field="customer_id",
        )
    clean_reference = _clean_text(reference)
    if clean_reference is not None and len(clean_reference) > 100:
        raise InvalidInputError(
            "The payment reference is too long (100 characters at most).", field="payment_reference"
        )
    return _Payment(split, method if split.paid > 0 else None, clean_reference)


def post_sale(
    session: Session,
    ctx: RequestContext,
    sale_id: int,
    *,
    amount_paid: Decimal | None = None,
    payment_method: PaymentMethod | None = None,
    payment_reference: str | None = None,
) -> SaleView:
    """Post a draft: settle the payment, number it, take the stock out, record the cost, charge any credit
    to the khata. All or nothing.

    `amount_paid=None` means paid in full. The sale row is locked first, so two requests posting the same
    draft cannot both succeed: the second finds it already POSTED and is refused. Products are then locked
    in a fixed order, and the stock is checked under those locks, so two sales cannot both take the last unit.
    """
    sale = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    if sale.status is SaleStatus.POSTED:
        raise ConflictError(f"This sale has already been posted ({sale.invoice_no}).")
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This sale is void and cannot be posted.")

    items = _items_of(session, ctx.shop_id, sale.id)
    if not items:
        raise InvalidInputError("Add at least one item before posting.", field="items")
    shop = get_shop(session, ctx.shop_id)
    today = shop_today(shop)
    if sale.sale_date > today:
        raise InvalidInputError("The sale date cannot be in the future.", field="sale_date")

    # Re-check every line as it is now (a product may have been deactivated since the cart was made), and
    # recompute the totals from the lines: nothing supplied earlier is trusted.
    lines: list[_Line] = []
    problems: list[tuple[str | None, str]] = []
    for index, item in enumerate(items):
        line, found = _clean_item(
            session,
            ctx.shop_id,
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount": item.discount,
            },
            f"items.{index}",
            shop.mrp_validation_mode,
        )
        problems.extend(found)
        if line is not None and line.unit.id != item.unit_id:
            problems.append(
                (
                    f"items.{index}.product_id",
                    f"The unit of '{line.product.name}' has changed. Re-add the line.",
                )
            )
        if line is not None:
            lines.append(line)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)

    totals = calc.bill_totals([line.total for line in lines], sale.discount)
    payment = _resolve_payment(totals.total, sale.customer_id, amount_paid, payment_method, payment_reference)

    before = _snapshot(sale, items)
    products = inventory_service.lock_products(session, ctx.shop_id, [i.product_id for i in items])

    requested: dict[int, Decimal] = {}
    for item in items:
        requested[item.product_id] = requested.get(item.product_id, ZERO) + item.quantity
    shortages = {s.product_id: s for s in inventory_service.find_shortages(session, ctx.shop_id, requested)}
    if shortages:
        errors = [
            (f"items.{index}.quantity", inventory_service.shortage_message(shortages[item.product_id]))
            for index, item in enumerate(items)
            if item.product_id in shortages
        ]
        raise ConflictError(errors[0][1], field=errors[0][0], errors=errors)

    for item in items:
        issue = inventory_service.issue_sale_line(
            session,
            ctx,
            product=products[item.product_id],
            quantity=item.quantity,
            sale_item_id=item.id,
            txn_date=sale.sale_date,
        )
        item.unit_cost = issue.unit_cost
        item.cogs_amount = issue.cogs

    fiscal_year = numbering_service.fiscal_year_label(today)
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, fiscal_year)
    sale.invoice_no = numbering_service.format_document_number(NUMBER_PREFIX, fiscal_year, number)
    sale.subtotal, sale.total_amount = totals.subtotal, totals.total
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
            reference_type=KhataReferenceType.SALE,
            reference_id=sale.id,
            entry_date=sale.sale_date,
            note=f"Sale {sale.invoice_no}",
        )
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=sale.id,
        action="post",
        before=before,
        after=_snapshot(sale, items),
    )
    return get_sale_view(session, ctx.shop_id, sale.id)


# --- Writing: voiding and correcting -------------------------------------------------------------


def void_sale(session: Session, ctx: RequestContext, sale_id: int, reason: str) -> SaleView:
    """Cancel a sale. A posted one is reversed (stock back on the shelf, credit off the khata); a draft is
    simply discarded."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise InvalidInputError("Give a reason for voiding this sale.", field="reason")
    if len(cleaned) > 500:
        raise InvalidInputError("The reason is too long (500 characters at most).", field="reason")

    sale = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    if sale.status is SaleStatus.VOID:
        raise ConflictError("This sale is already void.")

    before = _snapshot(sale)
    was_posted = sale.status is SaleStatus.POSTED
    if was_posted:
        live_returns = session.scalar(
            select(func.count()).where(
                SalesReturn.shop_id == ctx.shop_id,
                SalesReturn.sale_id == sale.id,
                SalesReturn.status != DocumentStatus.VOID,
            )
        )
        if live_returns:
            raise ConflictError("This sale has returns. Void the returns first, then void the sale.")
        item_ids = [i.id for i in _items_of(session, ctx.shop_id, sale.id)]
        note = f"Void of {sale.invoice_no}: {cleaned}"
        inventory_service.reverse_lines(
            session,
            ctx,
            reference_type=StockReferenceType.SALE_ITEM,
            reference_ids=item_ids,
            note=note,
        )
        khata_service.reverse_credit_sale(session, ctx, KhataReferenceType.SALE, sale.id, reason=note)
    sale.status = SaleStatus.VOID
    sale.void_reason = cleaned
    sale.voided_at = utc_now()
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=sale.id,
        action="void" if was_posted else "discard",
        before=before,
        after={**_snapshot(sale), "void_reason": cleaned},
    )
    return get_sale_view(session, ctx.shop_id, sale.id)


def correct_sale(session: Session, ctx: RequestContext, sale_id: int) -> SaleView:
    """Start a corrected copy of a voided sale, as a new draft (linked through `replaces_id`).

    The copy keeps the customer, notes, bill discount and lines, ready to edit and post; prices are kept as
    they were on the bill. A voided sale can be corrected once.
    """
    source = _get_sale(session, ctx.shop_id, sale_id, lock=True)
    if source.status is not SaleStatus.VOID:
        raise ConflictError("Void the sale first, then make a corrected copy of it.")
    if source.invoice_no is None:
        raise ConflictError("A discarded draft has nothing to correct. Start a new sale instead.")
    if session.scalar(select(Sale.id).where(Sale.shop_id == ctx.shop_id, Sale.replaces_id == source.id)):
        raise ConflictError("A corrected copy of this sale has already been made.")

    shop = get_shop(session, ctx.shop_id)
    draft = Sale(
        shop_id=ctx.shop_id,
        status=SaleStatus.DRAFT,
        customer_id=source.customer_id,
        sale_date=min(source.sale_date, shop_today(shop)),
        notes=source.notes,
        subtotal=ZERO,
        discount=ZERO,
        total_amount=ZERO,
        replaces_id=source.id,
        created_by=ctx.user_id,
    )
    session.add(draft)
    session.flush()
    for old in _items_of(session, ctx.shop_id, source.id):
        session.add(
            SaleItem(
                shop_id=ctx.shop_id,
                sale_id=draft.id,
                product_id=old.product_id,
                unit_id=old.unit_id,
                quantity=old.quantity,
                unit_price=old.unit_price,
                mrp=old.mrp,
                discount=old.discount,
                line_total=old.line_total,
            )
        )
    session.flush()
    _retotal(session, draft, discount=source.discount)
    record_audit(
        session,
        ctx,
        entity_type="sale",
        entity_id=draft.id,
        action="correct",
        before={"replaces": source.invoice_no},
        after=_snapshot(draft, _items_of(session, ctx.shop_id, draft.id)),
    )
    return get_sale_view(session, ctx.shop_id, draft.id)
