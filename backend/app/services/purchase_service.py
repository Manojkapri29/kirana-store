"""Purchases: goods bought from a supplier, from a draft to stock on the shelf.

Generic for every kind of business: nothing here depends on the business type.

Lifecycle (`Purchase.status`): DRAFT -> POSTED -> VOID.
  * A DRAFT is work in progress. It can be edited freely, has no number, and never touches stock or cost.
  * POSTING is the one moment things happen, all in a single database transaction: the purchase gets its
    number (PUR/2026-27/0001), each line adds a PURCHASE row to the stock ledger, the product's moving
    weighted average cost is updated, and the stock/cost snapshot is stored on each line. If anything fails,
    nothing is kept: no number is used, no stock moves. A purchase can be posted only once.
  * A POSTED purchase is never edited or deleted. VOIDING it writes REVERSAL ledger rows (refused if that
    stock has already gone) and rebuilds the average cost without it. Voiding a draft simply discards it.
  * To fix a posted purchase: void it, then "correct" it, which copies it into a new draft to edit and post.

Money rules: a line costs `round(quantity x unit cost) - discount`, in whole paise, half-up. The average
cost uses that net line total, so a discount lowers the cost of the goods. Stock is never stored: it is the
sum of the ledger, and this module writes to the ledger only through `inventory_service`.

Every query is scoped to the caller's shop; another shop's purchase, supplier or product is "not found".
Supplier payments and the supplier ledger are a later phase; nothing here records payments.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.context import RequestContext
from app.db.types import round_money, utc_now
from app.models import Product, Purchase, PurchaseItem, Supplier, Unit, User
from app.models.enums import PurchaseStatus, StockReferenceType
from app.services import inventory_service, numbering_service, purchase_return_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DOC_TYPE = "PURCHASE"
NUMBER_PREFIX = "PUR"
MAX_ITEMS = 200
ZERO_MONEY = Decimal("0.00")

HEADER_FIELDS = ("supplier_id", "supplier_invoice_no", "purchase_date", "notes")
ItemInput = dict[str, Any]  # product_id, quantity, unit_cost, and optionally discount and unit_id


@dataclass(frozen=True)
class PurchaseItemView:
    item: PurchaseItem
    product_sku: str
    product_name: str
    unit_code: str
    unit_name: str
    unit_allows_decimal: bool
    effects: list[inventory_service.LedgerEntry] = field(default_factory=list)  # ledger rows behind this line


@dataclass(frozen=True)
class PurchaseView:
    purchase: Purchase
    supplier_name: str
    created_by_name: str
    posted_by_name: str | None
    items: list[PurchaseItemView]
    replaced_by_id: int | None  # the corrected copy of this (voided) purchase, if one was made


@dataclass(frozen=True)
class PurchaseRow:
    purchase: Purchase
    supplier_name: str
    created_by_name: str
    item_count: int


@dataclass(frozen=True)
class PurchaseItemRow:
    """One line with its purchase, for the items export."""

    purchase: Purchase
    item: PurchaseItem
    supplier_name: str
    product_sku: str
    product_name: str
    unit_code: str


@dataclass(frozen=True)
class SupplierPurchaseTotals:
    posted_count: int
    posted_total: Decimal


# --- Reading -------------------------------------------------------------------------------------


def _get_purchase(session: Session, shop_id: int, purchase_id: int, *, lock: bool = False) -> Purchase:
    query = select(Purchase).where(Purchase.shop_id == shop_id, Purchase.id == purchase_id)
    purchase = session.scalar(query.with_for_update() if lock else query)
    if purchase is None:
        raise NotFoundError("Purchase not found")  # also the answer for another shop's purchase
    return purchase


def _items_of(session: Session, shop_id: int, purchase_id: int) -> list[PurchaseItem]:
    return list(
        session.scalars(
            select(PurchaseItem)
            .where(PurchaseItem.shop_id == shop_id, PurchaseItem.purchase_id == purchase_id)
            .order_by(PurchaseItem.id)
        )
    )


def get_purchase_view(session: Session, shop_id: int, purchase_id: int) -> PurchaseView:
    posted_by = aliased(User)
    row = session.execute(
        select(Purchase, Supplier.name, User.full_name, posted_by.full_name)
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .join(User, (User.shop_id == Purchase.shop_id) & (User.id == Purchase.created_by))
        .outerjoin(posted_by, (posted_by.shop_id == Purchase.shop_id) & (posted_by.id == Purchase.posted_by))
        .where(Purchase.shop_id == shop_id, Purchase.id == purchase_id)
        .execution_options(populate_existing=True)  # show what is stored, not the raw input
    ).first()
    if row is None:
        raise NotFoundError("Purchase not found")
    purchase, supplier_name, created_by_name, posted_by_name = row

    lines = session.execute(
        select(PurchaseItem, Product.sku, Product.name, Unit.code, Unit.name, Unit.allows_decimal)
        .join(Product, (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id))
        .join(Unit, Unit.id == PurchaseItem.unit_id)
        .where(PurchaseItem.shop_id == shop_id, PurchaseItem.purchase_id == purchase_id)
        .order_by(PurchaseItem.id)
        .execution_options(populate_existing=True)
    ).all()
    effects = inventory_service.entries_for_lines(
        session, shop_id, StockReferenceType.PURCHASE_ITEM, [line[0].id for line in lines]
    )
    items = [
        PurchaseItemView(item, sku, name, unit_code, unit_name, allows_decimal, effects[item.id])
        for item, sku, name, unit_code, unit_name, allows_decimal in lines
    ]
    replaced_by_id = session.scalar(
        select(Purchase.id).where(Purchase.shop_id == shop_id, Purchase.replaces_id == purchase_id)
    )
    return PurchaseView(purchase, supplier_name, created_by_name, posted_by_name, items, replaced_by_id)


def _search_clause(text: str) -> ColumnElement[bool]:
    """Match the purchase number, the supplier's invoice number, the supplier's name or the notes."""
    needle = text.strip().lower()
    return or_(
        func.lower(func.coalesce(Purchase.purchase_no, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Purchase.supplier_invoice_no, "")).contains(needle, autoescape=True),
        func.lower(Supplier.name).contains(needle, autoescape=True),
        func.lower(func.coalesce(Purchase.notes, "")).contains(needle, autoescape=True),
    )


def _filters(
    shop_id: int,
    *,
    q: str | None,
    supplier_id: int | None,
    statuses: Sequence[PurchaseStatus] | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Purchase.shop_id == shop_id]
    if q and q.strip():
        conditions.append(_search_clause(q))
    if supplier_id is not None:
        conditions.append(Purchase.supplier_id == supplier_id)
    if statuses:
        conditions.append(Purchase.status.in_(statuses))
    if date_from is not None:
        conditions.append(Purchase.purchase_date >= date_from)
    if date_to is not None:
        conditions.append(Purchase.purchase_date <= date_to)
    return conditions


def list_purchases(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    supplier_id: int | None = None,
    statuses: Sequence[PurchaseStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[PurchaseRow], int]:
    """Purchases, newest first. `limit=None` returns everything (used by exports)."""
    if date_from and date_to and date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    conditions = _filters(
        shop_id, q=q, supplier_id=supplier_id, statuses=statuses, date_from=date_from, date_to=date_to
    )
    join = (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id)
    total = session.scalar(select(func.count()).select_from(Purchase).join(Supplier, join).where(*conditions))

    item_count = (
        select(func.count())
        .where(PurchaseItem.shop_id == Purchase.shop_id, PurchaseItem.purchase_id == Purchase.id)
        .correlate(Purchase)
        .scalar_subquery()
    )
    query = (
        select(Purchase, Supplier.name, User.full_name, item_count)
        .join(Supplier, join)
        .join(User, (User.shop_id == Purchase.shop_id) & (User.id == Purchase.created_by))
        .where(*conditions)
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [PurchaseRow(*row) for row in session.execute(query)], total


def list_item_rows(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    supplier_id: int | None = None,
    statuses: Sequence[PurchaseStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    purchase_id: int | None = None,
) -> list[PurchaseItemRow]:
    """Every line of the matching purchases (or of one purchase), in purchase order. For the items export."""
    conditions = _filters(
        shop_id, q=q, supplier_id=supplier_id, statuses=statuses, date_from=date_from, date_to=date_to
    )
    if purchase_id is not None:
        conditions.append(Purchase.id == purchase_id)
    query = (
        select(Purchase, PurchaseItem, Supplier.name, Product.sku, Product.name, Unit.code)
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .join(
            PurchaseItem,
            (PurchaseItem.shop_id == Purchase.shop_id) & (PurchaseItem.purchase_id == Purchase.id),
        )
        .join(Product, (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id))
        .join(Unit, Unit.id == PurchaseItem.unit_id)
        .where(*conditions)
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc(), PurchaseItem.id)
    )
    return [PurchaseItemRow(*row) for row in session.execute(query)]


def supplier_totals(session: Session, shop_id: int, supplier_id: int) -> SupplierPurchaseTotals:
    """How many posted purchases a supplier has, and what they add up to (voided ones are left out)."""
    count, total = session.execute(
        select(func.count(), func.sum(Purchase.total_amount)).where(
            Purchase.shop_id == shop_id,
            Purchase.supplier_id == supplier_id,
            Purchase.status == PurchaseStatus.POSTED,
        )
    ).one()
    return SupplierPurchaseTotals(count, ZERO_MONEY if total is None else total)


# --- Validation ----------------------------------------------------------------------------------


def _clean_text(value: str | None, *, collapse: bool = False) -> str | None:
    """Trim; blank becomes None. `collapse` also squeezes inner runs of spaces (for short identifiers)."""
    if value is None:
        return None
    cleaned = " ".join(value.split()) if collapse else value.strip()
    return cleaned or None


def _path(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _check_supplier(session: Session, shop_id: int, supplier_id: int | None) -> None:
    if supplier_id is None:
        raise InvalidInputError("Choose the supplier.", field="supplier_id")
    supplier = session.scalar(select(Supplier).where(Supplier.shop_id == shop_id, Supplier.id == supplier_id))
    if supplier is None:
        raise InvalidInputError("Supplier not found.", field="supplier_id")
    if not supplier.is_active:
        raise InvalidInputError(
            f"'{supplier.name}' is inactive. Activate the supplier first, or choose another.",
            field="supplier_id",
        )


def _check_invoice_free(
    session: Session, shop_id: int, supplier_id: int, invoice_no: str | None, *, exclude_id: int | None
) -> None:
    """A supplier's invoice number may be used once among live (draft or posted) purchases."""
    if invoice_no is None:
        return
    query = select(Purchase.purchase_no, Purchase.id, Purchase.status).where(
        Purchase.shop_id == shop_id,
        Purchase.supplier_id == supplier_id,
        func.lower(Purchase.supplier_invoice_no) == invoice_no.lower(),
        Purchase.status != PurchaseStatus.VOID,
    )
    if exclude_id is not None:
        query = query.where(Purchase.id != exclude_id)
    clash = session.execute(query.limit(1)).first()
    if clash is not None:
        number, purchase_id, status = clash
        where = number if number else f"draft #{purchase_id}"
        raise ConflictError(
            f"This supplier's invoice {invoice_no} has already been entered "
            f"({where}, {status.value.lower()}). If that entry was a mistake, void it first.",
            field="supplier_invoice_no",
        )


def _clean_header(session: Session, shop_id: int, values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Validate the header fields present in `values` (all of them unless `partial`)."""
    out: dict[str, Any] = {}
    problems: list[tuple[str | None, str]] = []
    shop = get_shop(session, shop_id)

    def guard(step: Any) -> None:
        try:
            step()
        except InvalidInputError as exc:
            problems.extend(exc.errors)

    if not partial or "supplier_id" in values:
        guard(lambda: _check_supplier(session, shop_id, values.get("supplier_id")))
        out["supplier_id"] = values.get("supplier_id")
    if not partial or "purchase_date" in values:
        when = values.get("purchase_date") or shop_today(shop)
        if when > shop_today(shop):
            problems.append(("purchase_date", "The purchase date cannot be in the future."))
        out["purchase_date"] = when
    if "supplier_invoice_no" in values:
        invoice_no = _clean_text(values["supplier_invoice_no"], collapse=True)
        if invoice_no is not None and len(invoice_no) > 50:
            problems.append(
                ("supplier_invoice_no", "The invoice number is too long (50 characters at most).")
            )
        out["supplier_invoice_no"] = invoice_no
    if "notes" in values:
        out["notes"] = _clean_text(values["notes"])

    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


@dataclass(frozen=True)
class _Line:
    product: Product
    unit_id: int
    quantity: Decimal
    unit_cost: Decimal
    discount: Decimal
    line_total: Decimal


def compute_line_total(quantity: Decimal, unit_cost: Decimal, discount: Decimal) -> Decimal:
    """round(quantity x unit cost) - discount, in whole paise. ValueError if the discount is too big."""
    gross = round_money(quantity * unit_cost)
    if discount > gross:
        raise ValueError("discount above line amount")
    return gross - discount


def _clean_item(session: Session, shop_id: int, raw: ItemInput, prefix: str) -> tuple[_Line | None, list]:
    """Validate one line. Returns the cleaned line, or None plus every problem found."""
    problems: list[tuple[str | None, str]] = []
    product = None
    product_id = raw.get("product_id")
    if product_id is None:
        problems.append((_path(prefix, "product_id"), "Choose the product."))
    else:
        product = session.scalar(select(Product).where(Product.shop_id == shop_id, Product.id == product_id))
        if product is None:
            problems.append((_path(prefix, "product_id"), "Product not found."))
        elif not product.is_active:
            problems.append((_path(prefix, "product_id"), f"'{product.name}' is inactive."))

    quantity = raw.get("quantity")
    unit_cost = raw.get("unit_cost")
    discount = raw.get("discount") or ZERO_MONEY
    if quantity is None or quantity <= 0:
        problems.append((_path(prefix, "quantity"), "Quantity must be greater than zero."))
    if unit_cost is None:
        problems.append((_path(prefix, "unit_cost"), "Enter the price."))

    unit = None
    if product is not None:
        unit = session.scalars(select(Unit).where(Unit.id == product.unit_id)).one()
        if raw.get("unit_id") not in (None, product.unit_id):
            problems.append(
                (_path(prefix, "unit_id"), f"The unit must be {unit.name}, the product's own unit.")
            )
        if quantity is not None and quantity > 0:
            try:
                inventory_service.validate_quantity_for_unit(quantity, unit, field=_path(prefix, "quantity"))
            except InvalidInputError as exc:
                problems.extend(exc.errors)

    line_total = None
    if quantity is not None and quantity > 0 and unit_cost is not None:
        try:
            line_total = compute_line_total(quantity, unit_cost, discount)
        except ValueError:
            problems.append((_path(prefix, "discount"), "The discount is more than the line amount."))

    if problems or product is None or unit is None or line_total is None:
        return None, problems
    return _Line(product, product.unit_id, quantity, unit_cost, discount, line_total), []


def _clean_items(session: Session, shop_id: int, raw_items: Sequence[ItemInput]) -> list[_Line]:
    if len(raw_items) > MAX_ITEMS:
        raise InvalidInputError(f"A purchase can have at most {MAX_ITEMS} items.", field="items")
    lines: list[_Line] = []
    problems: list[tuple[str | None, str]] = []
    for index, raw in enumerate(raw_items):
        line, found = _clean_item(session, shop_id, raw, f"items.{index}")
        problems.extend(found)
        if line is not None:
            lines.append(line)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return lines


# --- Writing: drafts -----------------------------------------------------------------------------


def _snapshot(purchase: Purchase, items: Sequence[PurchaseItem] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": purchase.status,
        "purchase_no": purchase.purchase_no,
        "supplier_id": purchase.supplier_id,
        "supplier_invoice_no": purchase.supplier_invoice_no,
        "purchase_date": purchase.purchase_date,
        "total_amount": purchase.total_amount,
        "notes": purchase.notes,
    }
    if items is not None:
        data["items"] = [
            {
                "product_id": i.product_id,
                "quantity": i.quantity,
                "unit_cost": i.unit_cost,
                "discount": i.discount,
                "line_total": i.line_total,
            }
            for i in items
        ]
    return data


def _require_draft(purchase: Purchase) -> None:
    if purchase.status is PurchaseStatus.POSTED:
        raise ConflictError(
            "This purchase has been posted and can no longer be edited. "
            "Void it and make a corrected copy if something is wrong."
        )
    if purchase.status is PurchaseStatus.VOID:
        raise ConflictError("This purchase is void and cannot be edited.")


def _write_items(session: Session, ctx: RequestContext, purchase: Purchase, lines: Sequence[_Line]) -> None:
    for line in lines:
        session.add(
            PurchaseItem(
                shop_id=ctx.shop_id,
                purchase_id=purchase.id,
                product_id=line.product.id,
                unit_id=line.unit_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                discount=line.discount,
                line_total=line.line_total,
            )
        )
    session.flush()


def _retotal(session: Session, purchase: Purchase) -> None:
    """Keep the header total equal to the sum of the lines."""
    total = session.scalar(
        select(func.sum(PurchaseItem.line_total)).where(
            PurchaseItem.shop_id == purchase.shop_id, PurchaseItem.purchase_id == purchase.id
        )
    )
    purchase.total_amount = ZERO_MONEY if total is None else total
    session.flush()


def create_purchase(
    session: Session,
    ctx: RequestContext,
    header: dict[str, Any],
    items: Sequence[ItemInput] | None = None,
) -> PurchaseView:
    """Create a draft purchase, optionally with its lines. Nothing is posted and no stock moves."""
    problems: list[tuple[str | None, str]] = []
    values: dict[str, Any] = {}
    lines: list[_Line] = []
    try:
        values = _clean_header(session, ctx.shop_id, header, partial=False)
    except InvalidInputError as exc:
        problems.extend(exc.errors)
    try:
        lines = _clean_items(session, ctx.shop_id, items or [])
    except InvalidInputError as exc:
        problems.extend(exc.errors)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    _check_invoice_free(
        session, ctx.shop_id, values["supplier_id"], values.get("supplier_invoice_no"), exclude_id=None
    )

    purchase = Purchase(
        shop_id=ctx.shop_id,
        status=PurchaseStatus.DRAFT,
        created_by=ctx.user_id,
        total_amount=ZERO_MONEY,
        **{key: values.get(key) for key in HEADER_FIELDS if key in values},
    )
    session.add(purchase)
    session.flush()
    _write_items(session, ctx, purchase, lines)
    _retotal(session, purchase)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="create",
        after=_snapshot(purchase, _items_of(session, ctx.shop_id, purchase.id)),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def update_purchase(
    session: Session, ctx: RequestContext, purchase_id: int, changes: dict[str, Any]
) -> PurchaseView:
    """Change header fields of a draft (supplier, invoice number, date, notes)."""
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    _require_draft(purchase)
    values = _clean_header(session, ctx.shop_id, changes, partial=True)
    before = _snapshot(purchase)
    supplier_id = values.get("supplier_id", purchase.supplier_id)
    invoice_no = values.get("supplier_invoice_no", purchase.supplier_invoice_no)
    if "supplier_id" in values or "supplier_invoice_no" in values:
        _check_invoice_free(session, ctx.shop_id, supplier_id, invoice_no, exclude_id=purchase.id)
    for key, value in values.items():
        setattr(purchase, key, value)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="update",
        before=before,
        after=_snapshot(purchase),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def replace_items(
    session: Session, ctx: RequestContext, purchase_id: int, items: Sequence[ItemInput]
) -> PurchaseView:
    """Replace all lines of a draft with the given ones. This is how lines are edited or removed."""
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    _require_draft(purchase)
    lines = _clean_items(session, ctx.shop_id, items)
    before_items = _items_of(session, ctx.shop_id, purchase.id)
    before = _snapshot(purchase, before_items)
    # A draft's lines have no ledger rows yet, so replacing them is safe (the ledger is never involved).
    for old in before_items:
        session.delete(old)
    session.flush()
    _write_items(session, ctx, purchase, lines)
    _retotal(session, purchase)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="items_changed",
        before=before,
        after=_snapshot(purchase, _items_of(session, ctx.shop_id, purchase.id)),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def add_item(session: Session, ctx: RequestContext, purchase_id: int, item: ItemInput) -> PurchaseView:
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    _require_draft(purchase)
    existing = _items_of(session, ctx.shop_id, purchase.id)
    if len(existing) >= MAX_ITEMS:
        raise InvalidInputError(f"A purchase can have at most {MAX_ITEMS} items.", field="product_id")
    line, problems = _clean_item(session, ctx.shop_id, item, "")
    if line is None:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    before = _snapshot(purchase, existing)
    _write_items(session, ctx, purchase, [line])
    _retotal(session, purchase)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="items_changed",
        before=before,
        after=_snapshot(purchase, _items_of(session, ctx.shop_id, purchase.id)),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def update_item(
    session: Session, ctx: RequestContext, purchase_id: int, item_id: int, changes: ItemInput
) -> PurchaseView:
    """Change one line of a draft. Only the fields present in `changes` are replaced."""
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    _require_draft(purchase)
    existing = _items_of(session, ctx.shop_id, purchase.id)
    target = next((i for i in existing if i.id == item_id), None)
    if target is None:
        raise NotFoundError("Purchase item not found")
    merged: ItemInput = {
        "product_id": target.product_id,
        "quantity": target.quantity,
        "unit_cost": target.unit_cost,
        "discount": target.discount,
        "unit_id": target.unit_id,
        **changes,
    }
    if "product_id" in changes and changes["product_id"] != target.product_id:
        merged.pop("unit_id")  # a different product brings its own unit
    line, problems = _clean_item(session, ctx.shop_id, merged, "")
    if line is None:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    before = _snapshot(purchase, existing)
    target.product_id = line.product.id
    target.unit_id = line.unit_id
    target.quantity = line.quantity
    target.unit_cost = line.unit_cost
    target.discount = line.discount
    target.line_total = line.line_total
    session.flush()
    _retotal(session, purchase)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="items_changed",
        before=before,
        after=_snapshot(purchase, _items_of(session, ctx.shop_id, purchase.id)),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


# --- Writing: posting, voiding, correcting -------------------------------------------------------


def post_purchase(session: Session, ctx: RequestContext, purchase_id: int) -> PurchaseView:
    """Post a draft: number it, add the stock, update the average costs. All or nothing.

    The purchase row is locked first, so two requests posting the same draft cannot both succeed: the
    second finds it already POSTED and is refused. Products are then locked in a fixed order.
    """
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    if purchase.status is PurchaseStatus.POSTED:
        raise ConflictError(f"This purchase has already been posted ({purchase.purchase_no}).")
    if purchase.status is PurchaseStatus.VOID:
        raise ConflictError("This purchase is void and cannot be posted.")

    items = _items_of(session, ctx.shop_id, purchase.id)
    if not items:
        raise InvalidInputError("Add at least one item before posting.", field="items")
    _check_supplier(session, ctx.shop_id, purchase.supplier_id)

    shop = get_shop(session, ctx.shop_id)
    today = shop_today(shop)
    if purchase.purchase_date > today:
        raise InvalidInputError("The purchase date cannot be in the future.", field="purchase_date")

    before = _snapshot(purchase, items)
    products = inventory_service.lock_products(session, ctx.shop_id, [i.product_id for i in items])
    for item in items:
        product = products[item.product_id]
        if product.unit_id != item.unit_id:
            raise ConflictError(
                f"The unit of '{product.name}' has changed since this draft was made. "
                "Re-enter the line and post again.",
                field="items",
            )
        receipt = inventory_service.receive_purchase_line(
            session,
            ctx,
            product=product,
            quantity=item.quantity,
            line_total=item.line_total,
            purchase_item_id=item.id,
            txn_date=purchase.purchase_date,
        )
        item.stock_before = receipt.stock_before
        item.avg_cost_before = receipt.avg_cost_before
        item.avg_cost_after = receipt.avg_cost_after

    fiscal_year = numbering_service.fiscal_year_label(today)
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, fiscal_year)
    purchase.purchase_no = numbering_service.format_document_number(NUMBER_PREFIX, fiscal_year, number)
    purchase.status = PurchaseStatus.POSTED
    purchase.posted_at = utc_now()
    purchase.posted_by = ctx.user_id
    _retotal(session, purchase)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="post",
        before=before,
        after=_snapshot(purchase, items),
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def void_purchase(session: Session, ctx: RequestContext, purchase_id: int, reason: str) -> PurchaseView:
    """Cancel a purchase. A posted one is reversed in the stock ledger; a draft is simply discarded."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise InvalidInputError("Give a reason for voiding this purchase.", field="reason")
    if len(cleaned) > 500:
        raise InvalidInputError("The reason is too long (500 characters at most).", field="reason")

    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    if purchase.status is PurchaseStatus.VOID:
        raise ConflictError("This purchase is already void.")

    before = _snapshot(purchase)
    was_posted = purchase.status is PurchaseStatus.POSTED
    if was_posted and purchase_return_service.has_live_returns(session, ctx.shop_id, purchase.id):
        raise ConflictError("This purchase has returns. Void the returns first, then void the purchase.")
    if was_posted:
        item_ids = [i.id for i in _items_of(session, ctx.shop_id, purchase.id)]
        inventory_service.reverse_lines(
            session,
            ctx,
            reference_type=StockReferenceType.PURCHASE_ITEM,
            reference_ids=item_ids,
            note=f"Void of {purchase.purchase_no}: {cleaned}",
        )
    purchase.status = PurchaseStatus.VOID
    purchase.void_reason = cleaned
    purchase.voided_at = utc_now()
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=purchase.id,
        action="void" if was_posted else "discard",
        before=before,
        after={**_snapshot(purchase), "void_reason": cleaned},
    )
    return get_purchase_view(session, ctx.shop_id, purchase.id)


def correct_purchase(session: Session, ctx: RequestContext, purchase_id: int) -> PurchaseView:
    """Start a corrected copy of a voided purchase, as a new draft (linked through `replaces_id`).

    The copy keeps the supplier, invoice number, date, notes and lines, ready to edit and post. A voided
    purchase can be corrected once.
    """
    source = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    if source.status is not PurchaseStatus.VOID:
        raise ConflictError("Void the purchase first, then make a corrected copy of it.")
    if source.purchase_no is None:
        raise ConflictError("A discarded draft has nothing to correct. Start a new purchase instead.")
    if session.scalar(
        select(Purchase.id).where(Purchase.shop_id == ctx.shop_id, Purchase.replaces_id == source.id)
    ):
        raise ConflictError("A corrected copy of this purchase has already been made.")
    _check_supplier(session, ctx.shop_id, source.supplier_id)
    _check_invoice_free(
        session, ctx.shop_id, source.supplier_id, source.supplier_invoice_no, exclude_id=source.id
    )

    shop = get_shop(session, ctx.shop_id)
    draft = Purchase(
        shop_id=ctx.shop_id,
        status=PurchaseStatus.DRAFT,
        supplier_id=source.supplier_id,
        supplier_invoice_no=source.supplier_invoice_no,
        purchase_date=min(source.purchase_date, shop_today(shop)),
        notes=source.notes,
        total_amount=ZERO_MONEY,
        replaces_id=source.id,
        created_by=ctx.user_id,
    )
    session.add(draft)
    session.flush()
    for old in _items_of(session, ctx.shop_id, source.id):
        session.add(
            PurchaseItem(
                shop_id=ctx.shop_id,
                purchase_id=draft.id,
                product_id=old.product_id,
                unit_id=old.unit_id,
                quantity=old.quantity,
                unit_cost=old.unit_cost,
                discount=old.discount,
                line_total=old.line_total,
            )
        )
    session.flush()
    _retotal(session, draft)
    record_audit(
        session,
        ctx,
        entity_type="purchase",
        entity_id=draft.id,
        action="correct",
        before={"replaces": source.purchase_no},
        after=_snapshot(draft, _items_of(session, ctx.shop_id, draft.id)),
    )
    return get_purchase_view(session, ctx.shop_id, draft.id)
