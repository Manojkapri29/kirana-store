"""Purchase returns: goods sent back to a supplier from a posted purchase.

A return refers to the original purchase LINES (BUSINESS_RULES R1, R3): the total returned per line can
never exceed what was bought, and goods that have already been sold cannot be sent back. Posting is one
transaction: the purchase row is locked first, the stock is re-checked under a lock on each product, and
the goods leave the shelf through `inventory_service` at the cost they came in at (the line's net cost per
unit). The credit is the line's net amount in proportion to the quantity, worked out cumulatively so the
last return of a line takes the exact remainder (`return_calculation`).

Credit modes: CASH, UPI, or SUPPLIER_CREDIT. There is no supplier ledger yet, so SUPPLIER_CREDIT is
recorded on the return only. A return is never edited or deleted; a wrong one is voided, which puts the
goods back on the shelf. Voiding a purchase is refused while it has live returns. Every query is scoped to
the caller's shop.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import round_money, utc_now
from app.models import (
    Product,
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    PurchaseReturnItem,
    Supplier,
    Unit,
    User,
)
from app.models.enums import DocumentStatus, PurchaseStatus, StockReferenceType, SupplierCreditMode
from app.services import finance_period_service, inventory_service, numbering_service
from app.services import return_calculation as calc
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DOC_TYPE = "PURCHASE_RETURN"
NUMBER_PREFIX = "PRT"
MAX_ITEMS = 200
ZERO = calc.ZERO

ItemInput = dict[str, Any]  # purchase_item_id, quantity


@dataclass(frozen=True)
class _Basis:
    item: PurchaseItem
    product: Product
    unit: Unit
    returned_qty: Decimal
    credited: Decimal

    @property
    def returnable(self) -> Decimal:
        return self.item.quantity - self.returned_qty


@dataclass(frozen=True)
class PreviewLine:
    purchase_item_id: int | None
    product_name: str | None
    sku: str | None
    unit_code: str | None
    bought: Decimal | None
    already_returned: Decimal | None
    returnable: Decimal | None
    in_stock: Decimal | None  # on the shelf now: goods already sold cannot be sent back
    quantity: Decimal | None
    credit: Decimal | None
    errors: list[tuple[str, str]]


@dataclass(frozen=True)
class Preview:
    purchase_id: int
    lines: list[PreviewLine]
    total_credit: Decimal


@dataclass(frozen=True)
class ReturnLineView:
    item: PurchaseReturnItem
    product_name: str
    sku: str
    unit_code: str
    bought_quantity: Decimal


@dataclass(frozen=True)
class PurchaseReturnView:
    ret: PurchaseReturn
    purchase_no: str
    supplier_id: int
    supplier_name: str
    created_by_name: str
    items: list[ReturnLineView]


@dataclass(frozen=True)
class ReturnRow:
    ret: PurchaseReturn
    purchase_no: str
    supplier_name: str
    item_count: int


# --- Reading -------------------------------------------------------------------------------------


def _get_purchase(session: Session, shop_id: int, purchase_id: int, *, lock: bool = False) -> Purchase:
    query = select(Purchase).where(Purchase.shop_id == shop_id, Purchase.id == purchase_id)
    purchase = session.scalar(query.with_for_update() if lock else query)
    if purchase is None:
        raise NotFoundError("Purchase not found")  # also the answer for another shop's purchase
    return purchase


def _require_posted(purchase: Purchase) -> None:
    if purchase.status is not PurchaseStatus.POSTED:
        raise ConflictError("Only a posted purchase can have items returned.")


def _bases(session: Session, shop_id: int, purchase: Purchase) -> dict[int, _Basis]:
    rows = session.execute(
        select(PurchaseItem, Product, Unit)
        .join(Product, (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id))
        .join(Unit, Unit.id == PurchaseItem.unit_id)
        .where(PurchaseItem.shop_id == shop_id, PurchaseItem.purchase_id == purchase.id)
        .order_by(PurchaseItem.id)
    ).all()
    returned = {
        item_id: (qty, credit)
        for item_id, qty, credit in session.execute(
            select(
                PurchaseReturnItem.purchase_item_id,
                func.sum(PurchaseReturnItem.quantity),
                func.sum(PurchaseReturnItem.line_total),
            )
            .join(
                PurchaseReturn,
                (PurchaseReturn.shop_id == PurchaseReturnItem.shop_id)
                & (PurchaseReturn.id == PurchaseReturnItem.purchase_return_id),
            )
            .where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.purchase_id == purchase.id,
                PurchaseReturn.status == DocumentStatus.POSTED,
            )
            .group_by(PurchaseReturnItem.purchase_item_id)
        )
    }
    out: dict[int, _Basis] = {}
    for item, product, unit in rows:
        qty, credit = returned.get(item.id, (ZERO, ZERO))
        out[item.id] = _Basis(item, product, unit, qty or ZERO, credit or ZERO)
    return out


def _clean_lines(
    session: Session, shop_id: int, bases: dict[int, _Basis], raw_items: Sequence[ItemInput]
) -> tuple[list[tuple[_Basis, Decimal, Decimal]], list[PreviewLine], list[tuple[str | None, str]]]:
    problems: list[tuple[str | None, str]] = []
    preview: list[PreviewLine] = []
    good: list[tuple[_Basis, Decimal, Decimal]] = []
    seen: set[int] = set()
    stock = inventory_service.get_stock_map(session, shop_id, sorted({b.product.id for b in bases.values()}))
    for index, raw in enumerate(raw_items[:MAX_ITEMS]):
        path = f"items.{index}"
        found: list[tuple[str, str]] = []
        item_id, quantity = raw.get("purchase_item_id"), raw.get("quantity")
        basis = bases.get(item_id) if item_id is not None else None
        if basis is None:
            found.append((f"{path}.purchase_item_id", "This line is not part of the purchase."))
        elif item_id in seen:
            found.append((f"{path}.purchase_item_id", "This line is listed twice."))
        if quantity is None or quantity <= 0:
            found.append((f"{path}.quantity", "Quantity must be greater than zero."))
        credit: Decimal | None = None
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
                    bought = f"{basis.item.quantity.normalize():f} bought"
                    back = f"{basis.returned_qty.normalize():f} already returned"
                    message = (
                        f"Only {left} of '{basis.product.name}' can still be returned ({bought}, {back})."
                    )
                    found.append((f"{path}.quantity", message))
                elif not found:
                    credit = calc.refund_for(
                        basis.item.line_total,
                        basis.item.quantity,
                        basis.returned_qty,
                        basis.credited,
                        quantity,
                    )
                    good.append((basis, quantity, credit))
        problems.extend(found)
        preview.append(
            PreviewLine(
                purchase_item_id=item_id,
                product_name=None if basis is None else basis.product.name,
                sku=None if basis is None else basis.product.sku,
                unit_code=None if basis is None else basis.unit.code,
                bought=None if basis is None else basis.item.quantity,
                already_returned=None if basis is None else basis.returned_qty,
                returnable=None if basis is None else basis.returnable,
                in_stock=None if basis is None else stock.get(basis.product.id),
                quantity=quantity,
                credit=credit,
                errors=[(p.rsplit(".", 1)[-1], m) for p, m in found],
            )
        )
    return good, preview, problems


def calculate_preview(
    session: Session, shop_id: int, purchase_id: int, raw_items: Sequence[ItemInput]
) -> Preview:
    """What a return would credit, without saving anything. Lines are marked, not raised."""
    purchase = _get_purchase(session, shop_id, purchase_id)
    _require_posted(purchase)
    good, preview, _ = _clean_lines(session, shop_id, _bases(session, shop_id, purchase), raw_items)
    return Preview(purchase.id, preview, sum((credit for _, _, credit in good), ZERO))


def get_view(session: Session, shop_id: int, return_id: int) -> PurchaseReturnView:
    row = session.execute(
        select(PurchaseReturn, Purchase.purchase_no, Supplier.id, Supplier.name, User.full_name)
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .join(User, (User.shop_id == PurchaseReturn.shop_id) & (User.id == PurchaseReturn.created_by))
        .where(PurchaseReturn.shop_id == shop_id, PurchaseReturn.id == return_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise NotFoundError("Return not found")
    ret, purchase_no, supplier_id, supplier_name, created_by = row
    lines = session.execute(
        select(PurchaseReturnItem, Product.name, Product.sku, Unit.code, PurchaseItem.quantity)
        .join(
            PurchaseItem,
            (PurchaseItem.shop_id == PurchaseReturnItem.shop_id)
            & (PurchaseItem.id == PurchaseReturnItem.purchase_item_id),
        )
        .join(
            Product,
            (Product.shop_id == PurchaseReturnItem.shop_id) & (Product.id == PurchaseReturnItem.product_id),
        )
        .join(Unit, Unit.id == PurchaseItem.unit_id)
        .where(PurchaseReturnItem.shop_id == shop_id, PurchaseReturnItem.purchase_return_id == return_id)
        .order_by(PurchaseReturnItem.id)
        .execution_options(populate_existing=True)
    ).all()
    return PurchaseReturnView(
        ret, purchase_no, supplier_id, supplier_name, created_by, [ReturnLineView(*line) for line in lines]
    )


def list_returns(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    purchase_id: int | None = None,
    statuses: Sequence[DocumentStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[ReturnRow], int]:
    if date_from and date_to and date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    conditions: list[ColumnElement[bool]] = [PurchaseReturn.shop_id == shop_id]
    if q and q.strip():
        needle = q.strip().lower()
        conditions.append(
            or_(
                func.lower(PurchaseReturn.return_no).contains(needle, autoescape=True),
                func.lower(func.coalesce(Purchase.purchase_no, "")).contains(needle, autoescape=True),
                func.lower(Supplier.name).contains(needle, autoescape=True),
            )
        )
    if purchase_id is not None:
        conditions.append(PurchaseReturn.purchase_id == purchase_id)
    if statuses:
        conditions.append(PurchaseReturn.status.in_(statuses))
    if date_from is not None:
        conditions.append(PurchaseReturn.return_date >= date_from)
    if date_to is not None:
        conditions.append(PurchaseReturn.return_date <= date_to)
    joined = (
        select(PurchaseReturn)
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(*conditions)
    )
    total = session.scalar(select(func.count()).select_from(joined.subquery())) or 0
    item_count = (
        select(func.count())
        .where(
            PurchaseReturnItem.shop_id == PurchaseReturn.shop_id,
            PurchaseReturnItem.purchase_return_id == PurchaseReturn.id,
        )
        .correlate(PurchaseReturn)
        .scalar_subquery()
    )
    query = (
        select(PurchaseReturn, Purchase.purchase_no, Supplier.name, item_count)
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(*conditions)
        .order_by(PurchaseReturn.return_date.desc(), PurchaseReturn.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [ReturnRow(*row) for row in session.execute(query)], total


def has_live_returns(session: Session, shop_id: int, purchase_id: int) -> bool:
    """Used by `purchase_service` to refuse voiding a purchase whose goods have (partly) been sent back."""
    return (
        session.scalar(
            select(func.count()).where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.purchase_id == purchase_id,
                PurchaseReturn.status == DocumentStatus.POSTED,
            )
        )
        or 0
    ) > 0


# --- Writing -------------------------------------------------------------------------------------


def _snapshot(ret: PurchaseReturn) -> dict[str, Any]:
    return {
        "return_no": ret.return_no,
        "purchase_id": ret.purchase_id,
        "status": ret.status,
        "return_date": ret.return_date,
        "credit_mode": ret.credit_mode,
        "total_amount": ret.total_amount,
        "reason": ret.reason,
    }


def create_purchase_return(
    session: Session,
    ctx: RequestContext,
    purchase_id: int,
    *,
    items: Sequence[ItemInput],
    credit_mode: SupplierCreditMode,
    return_date: date | None = None,
    reason: str | None = None,
) -> PurchaseReturnView:
    """Post a return: goods leave the shelf and go back to the supplier, all or nothing."""
    purchase = _get_purchase(session, ctx.shop_id, purchase_id, lock=True)
    _require_posted(purchase)
    today = shop_today(get_shop(session, ctx.shop_id))
    problems: list[tuple[str | None, str]] = []
    if not items:
        problems.append(("items", "Choose at least one item to return."))
    if len(items) > MAX_ITEMS:
        problems.append(("items", f"A return can have at most {MAX_ITEMS} items."))
    when = return_date or today
    finance_period_service.assert_open(session, ctx, when, action="purchase_return")
    if when > today:
        problems.append(("return_date", "The return date cannot be in the future."))
    elif when < purchase.purchase_date:
        problems.append(("return_date", "The return date cannot be before the purchase."))
    cleaned_reason = (reason or "").strip() or None
    if cleaned_reason and len(cleaned_reason) > 500:
        problems.append(("reason", "The reason is too long (500 characters at most)."))
    good, _, line_problems = _clean_lines(session, ctx.shop_id, _bases(session, ctx.shop_id, purchase), items)
    problems.extend(line_problems)
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)

    products = inventory_service.lock_products(session, ctx.shop_id, [b.product.id for b, _, _ in good])
    fiscal_year = numbering_service.fiscal_year_label(today)
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, fiscal_year)
    ret = PurchaseReturn(
        shop_id=ctx.shop_id,
        return_no=numbering_service.format_document_number(NUMBER_PREFIX, fiscal_year, number),
        purchase_id=purchase.id,
        return_date=when,
        credit_mode=credit_mode,
        total_amount=sum((credit for _, _, credit in good), ZERO),
        reason=cleaned_reason,
        status=DocumentStatus.POSTED,
        created_by=ctx.user_id,
    )
    session.add(ret)
    session.flush()
    stock_errors: list[tuple[str | None, str]] = []
    for index, (basis, quantity, credit) in enumerate(good):
        unit_cost = round_money(basis.item.line_total / basis.item.quantity)
        line = PurchaseReturnItem(
            shop_id=ctx.shop_id,
            purchase_return_id=ret.id,
            purchase_item_id=basis.item.id,
            product_id=basis.product.id,
            quantity=quantity,
            unit_cost=unit_cost,
            line_total=credit,
        )
        session.add(line)
        session.flush()
        try:
            inventory_service.issue_purchase_return_line(
                session,
                ctx,
                product=products[basis.product.id],
                quantity=quantity,
                purchase_return_item_id=line.id,
                unit_cost=unit_cost,
                txn_date=when,
            )
        except ConflictError as exc:
            stock_errors.append((f"items.{index}.quantity", exc.message))
    if stock_errors:
        raise ConflictError(
            stock_errors[0][1], field=stock_errors[0][0], errors=stock_errors, code="insufficient_stock"
        )
    record_audit(
        session, ctx, entity_type="purchase_return", entity_id=ret.id, action="post", after=_snapshot(ret)
    )
    return get_view(session, ctx.shop_id, ret.id)


def void_purchase_return(
    session: Session, ctx: RequestContext, return_id: int, reason: str
) -> PurchaseReturnView:
    """Cancel a return: the goods come back onto the shelf. The return and its number stay on record."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise InvalidInputError("Give a reason for voiding this return.", field="reason")
    if len(cleaned) > 500:
        raise InvalidInputError("The reason is too long (500 characters at most).", field="reason")
    ret = session.scalar(
        select(PurchaseReturn)
        .where(PurchaseReturn.shop_id == ctx.shop_id, PurchaseReturn.id == return_id)
        .with_for_update()
    )
    if ret is None:
        raise NotFoundError("Return not found")
    if ret.status is DocumentStatus.VOID:
        raise ConflictError("This return is already void.")
    finance_period_service.assert_open(session, ctx, ret.return_date, action="void_purchase_return")
    before = _snapshot(ret)
    item_ids = list(
        session.scalars(
            select(PurchaseReturnItem.id).where(
                PurchaseReturnItem.shop_id == ctx.shop_id, PurchaseReturnItem.purchase_return_id == ret.id
            )
        )
    )
    inventory_service.reverse_lines(
        session,
        ctx,
        reference_type=StockReferenceType.PURCHASE_RETURN_ITEM,
        reference_ids=item_ids,
        note=f"Void of {ret.return_no}: {cleaned}",
    )
    ret.status = DocumentStatus.VOID
    ret.void_reason = cleaned
    ret.voided_at = utc_now()
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="purchase_return",
        entity_id=ret.id,
        action="void",
        before=before,
        after={**_snapshot(ret), "void_reason": cleaned},
    )
    return get_view(session, ctx.shop_id, ret.id)
