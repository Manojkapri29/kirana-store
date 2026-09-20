"""inventory_service: the ONLY module allowed to write to (or read) `inventory_transactions`.

Why one module: stock is derived from the ledger, so every rule about it (signs, no negative stock, one
transaction per business action, insert-only history) lives here where it can be tested thoroughly.
`tests/test_architecture.py` fails if any other module touches the ledger table.

Phase 3 implements:
    - opening stock (the starting balance of a product)
    - stock adjustments (service level only; the screens and API for them arrive in Phase 10)
    - stock queries: current stock, stock for many products, the inventory list, transaction history
    - the stock status (in stock / low stock / out of stock)
Purchases, sales, returns and reversals are added in later phases through this same module.

Transaction rule: nothing here commits. The caller owns the transaction (`write_transaction()`), so the
stock check and the ledger insert always happen atomically.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import from_thousandths, paise_to_rupees, rupees_to_paise, to_thousandths
from app.models import Category, InventoryTransaction, Product, Shop, Unit, User
from app.models.enums import AdjustmentReason, InventoryTxnType, StockReferenceType
from app.services._filters import product_search_clause
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0.000")


class StockStatus(StrEnum):
    IN_STOCK = "IN_STOCK"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


def stock_status(current_stock: Decimal, reorder_level: Decimal) -> StockStatus:
    """Out of stock at zero (or below); low when at or under the reorder level; otherwise in stock."""
    if current_stock <= 0:
        return StockStatus.OUT_OF_STOCK
    if current_stock <= reorder_level:
        return StockStatus.LOW_STOCK
    return StockStatus.IN_STOCK


def validate_quantity_for_unit(quantity: Decimal, unit: Unit, *, field: str) -> None:
    """A fractional quantity is only allowed for units that allow decimals (2.5 kg yes, 2.5 pieces no)."""
    if not unit.allows_decimal and quantity != quantity.to_integral_value():
        raise InvalidInputError(
            f"{unit.name} cannot be split: enter a whole number.",
            field=field,
        )


# --- Reading stock -------------------------------------------------------------------------------


def get_stock(session: Session, shop_id: int, product_id: int) -> Decimal:
    total = session.scalar(
        select(func.sum(InventoryTransaction.qty_delta)).where(
            InventoryTransaction.shop_id == shop_id, InventoryTransaction.product_id == product_id
        )
    )
    return ZERO if total is None else total


def get_stock_map(session: Session, shop_id: int, product_ids: Sequence[int]) -> dict[int, Decimal]:
    """Current stock for several products in one query. Products without movements have stock 0."""
    stock = {product_id: ZERO for product_id in product_ids}
    if not product_ids:
        return stock
    rows = session.execute(
        select(InventoryTransaction.product_id, func.sum(InventoryTransaction.qty_delta))
        .where(InventoryTransaction.shop_id == shop_id, InventoryTransaction.product_id.in_(product_ids))
        .group_by(InventoryTransaction.product_id)
    )
    for product_id, total in rows:
        stock[product_id] = total
    return stock


def count_movements(session: Session, shop_id: int, product_id: int) -> int:
    return session.scalar(
        select(func.count()).where(
            InventoryTransaction.shop_id == shop_id, InventoryTransaction.product_id == product_id
        )
    )


@dataclass(frozen=True)
class InventoryRow:
    product_id: int
    sku: str
    name: str
    brand: str | None
    barcode: str | None
    category_name: str
    unit_code: str
    unit_name: str
    allows_decimal: bool
    current_stock: Decimal
    reorder_level: Decimal
    avg_cost: Decimal | None
    status: StockStatus
    is_active: bool


def _status_filter_clause(stock: ColumnElement[Any], wanted: StockStatus) -> ColumnElement[bool]:
    if wanted is StockStatus.OUT_OF_STOCK:
        return stock <= 0
    if wanted is StockStatus.LOW_STOCK:
        return and_(stock > 0, stock <= Product.reorder_level)
    return stock > Product.reorder_level


def list_inventory(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    category_id: int | None = None,
    active: bool | None = True,
    status: StockStatus | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[InventoryRow], int]:
    """Inventory for all products of a shop, with current stock derived from the ledger."""
    totals = (
        select(
            InventoryTransaction.product_id.label("product_id"),
            func.sum(InventoryTransaction.qty_delta).label("stock"),
        )
        .where(InventoryTransaction.shop_id == shop_id)
        .group_by(InventoryTransaction.product_id)
        .subquery()
    )
    stock = func.coalesce(totals.c.stock, 0)

    conditions: list[ColumnElement[bool]] = [Product.shop_id == shop_id]
    if q and q.strip():
        conditions.append(product_search_clause(q))
    if category_id is not None:
        conditions.append(Product.category_id == category_id)
    if active is not None:
        conditions.append(Product.is_active.is_(active))
    if status is not None:
        conditions.append(_status_filter_clause(stock, status))

    base = (
        select(Product, Category.name, Unit, stock.label("current_stock"))
        .join(Category, and_(Category.shop_id == Product.shop_id, Category.id == Product.category_id))
        .join(Unit, Unit.id == Product.unit_id)
        .outerjoin(totals, totals.c.product_id == Product.id)
        .where(*conditions)
    )
    total_count = session.scalar(
        select(func.count())
        .select_from(Product)
        .outerjoin(totals, totals.c.product_id == Product.id)
        .where(*conditions)
    )
    query = base.order_by(func.lower(Product.name), Product.id).offset(offset)
    if limit is not None:
        query = query.limit(limit)

    rows = [
        InventoryRow(
            product_id=product.id,
            sku=product.sku,
            name=product.name,
            brand=product.brand,
            barcode=product.barcode,
            category_name=category_name,
            unit_code=unit.code,
            unit_name=unit.name,
            allows_decimal=unit.allows_decimal,
            current_stock=current,
            reorder_level=product.reorder_level,
            avg_cost=product.avg_cost,
            status=stock_status(current, product.reorder_level),
            is_active=product.is_active,
        )
        for product, category_name, unit, current in session.execute(query)
    ]
    return rows, total_count


# --- Transaction history -------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionRow:
    id: int
    product_id: int
    sku: str
    product_name: str
    txn_type: InventoryTxnType
    qty_delta: Decimal
    balance_after: Decimal
    unit_cost: Decimal | None
    txn_date: date
    reference_type: StockReferenceType | None
    reference_id: int | None
    reason_code: AdjustmentReason | None
    note: str | None
    created_by_name: str
    created_at: datetime


def list_transactions(
    session: Session,
    shop_id: int,
    *,
    product_id: int | None = None,
    txn_type: InventoryTxnType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    newest_first: bool = True,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[TransactionRow], int]:
    """Ledger history with a running balance per product ("what came in, what went out, what remains")."""
    t = InventoryTransaction
    # The running balance is computed over the product's FULL history, then filters narrow the rows shown.
    balance = func.sum(t.qty_delta).over(partition_by=t.product_id, order_by=(t.txn_date, t.id))
    history = (
        select(
            t.id,
            t.product_id,
            Product.sku.label("sku"),
            Product.name.label("product_name"),
            t.txn_type,
            t.qty_delta,
            balance.label("balance_after"),
            t.unit_cost,
            t.txn_date,
            t.reference_type,
            t.reference_id,
            t.reason_code,
            t.note,
            User.full_name.label("created_by_name"),
            t.created_at,
        )
        .join(Product, and_(Product.shop_id == t.shop_id, Product.id == t.product_id))
        .join(User, and_(User.shop_id == t.shop_id, User.id == t.created_by))
        .where(t.shop_id == shop_id)
    )
    if product_id is not None:
        history = history.where(t.product_id == product_id)
    h = history.subquery()

    conditions: list[ColumnElement[bool]] = []
    if txn_type is not None:
        conditions.append(h.c.txn_type == txn_type)
    if date_from is not None:
        conditions.append(h.c.txn_date >= date_from)
    if date_to is not None:
        conditions.append(h.c.txn_date <= date_to)

    total = session.scalar(select(func.count()).select_from(h).where(*conditions))
    order = (h.c.txn_date.desc(), h.c.id.desc()) if newest_first else (h.c.txn_date, h.c.id)
    query = select(h).where(*conditions).order_by(*order).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return [TransactionRow(**row._mapping) for row in session.execute(query)], total


# --- Writing to the ledger -----------------------------------------------------------------------


def _lock_product(session: Session, shop_id: int, product_id: int) -> Product:
    """Load the product for this shop and lock its row for the rest of the transaction.

    `with_for_update` is what protects "check stock, then change stock" on PostgreSQL. SQLite ignores it,
    because there the write transaction already holds the database-wide write lock (`BEGIN IMMEDIATE`).
    A product of another shop is reported as not found.
    """
    product = session.scalar(
        select(Product).where(Product.shop_id == shop_id, Product.id == product_id).with_for_update()
    )
    if product is None:
        raise NotFoundError("Product not found")
    return product


def _unit_of(session: Session, product: Product) -> Unit:
    return session.scalars(select(Unit).where(Unit.id == product.unit_id)).one()


def _require_active(product: Product) -> None:
    if not product.is_active:
        raise ConflictError(
            f"'{product.name}' is inactive. Activate the product before recording stock for it.",
            field="product_id",
        )


def _check_date(txn_date: date | None, shop: Shop) -> date:
    today = shop_today(shop)
    if txn_date is None:
        return today
    if txn_date > today:
        raise InvalidInputError("The date cannot be in the future.", field="txn_date")
    return txn_date


def record_opening_stock(
    session: Session,
    ctx: RequestContext,
    *,
    product_id: int,
    quantity: Decimal,
    unit_cost: Decimal | None = None,
    txn_date: date | None = None,
    note: str | None = None,
) -> InventoryTransaction:
    """Record the starting stock of a product as an OPENING ledger row.

    Rules:
      * quantity must be greater than zero (an opening balance is never negative);
      * a product has at most one OPENING row, and it must come before any other stock movement, so it
        is always the true starting balance. Later corrections are adjustments;
      * fractions only for units that allow them; the product must be active;
      * `unit_cost` is optional. If omitted the cost stays unknown (NULL), never 0.
    """
    product = _lock_product(session, ctx.shop_id, product_id)
    _require_active(product)
    shop = get_shop(session, ctx.shop_id)

    if quantity <= 0:
        raise InvalidInputError("Opening stock must be greater than zero.", field="quantity")
    validate_quantity_for_unit(quantity, _unit_of(session, product), field="quantity")
    if unit_cost is not None and unit_cost < 0:
        raise InvalidInputError("Cost cannot be negative.", field="unit_cost")
    when = _check_date(txn_date, shop)

    existing = session.scalars(
        select(InventoryTransaction.txn_type).where(
            InventoryTransaction.shop_id == ctx.shop_id, InventoryTransaction.product_id == product.id
        )
    ).all()
    if InventoryTxnType.OPENING in existing:
        raise ConflictError(
            "Opening stock has already been recorded for this product. "
            "To correct the quantity, use a stock adjustment.",
            field="product_id",
        )
    if existing:
        raise ConflictError(
            "Opening stock must be recorded before any other stock movement of the product. "
            "To correct the quantity, use a stock adjustment.",
            field="product_id",
        )

    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.OPENING,
        qty_delta=quantity,
        unit_cost=unit_cost,
        txn_date=when,
        reference_type=StockReferenceType.PRODUCT,
        reference_id=product.id,
        note=note,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)

    # This is the first stock the product ever had, so the opening cost is its average cost. From
    # Phase 5 the costing service maintains the moving weighted average.
    before_cost = product.avg_cost
    if unit_cost is not None:
        product.avg_cost = unit_cost
    record_audit(
        session,
        ctx,
        entity_type="product",
        entity_id=product.id,
        action="opening_stock",
        before={"avg_cost": before_cost, "stock": ZERO},
        after={
            "avg_cost": None
            if product.avg_cost is None
            else paise_to_rupees(rupees_to_paise(product.avg_cost)),
            "stock": from_thousandths(to_thousandths(quantity)),
            "unit_cost": None if unit_cost is None else paise_to_rupees(rupees_to_paise(unit_cost)),
            "txn_date": when,
        },  # stored formatting, e.g. "20.000" and "200.00"
    )
    return row


def record_adjustment(
    session: Session,
    ctx: RequestContext,
    *,
    product_id: int,
    quantity_delta: Decimal,
    reason_code: AdjustmentReason,
    note: str | None = None,
    txn_date: date | None = None,
) -> InventoryTransaction:
    """Add or remove stock with a mandatory reason. Service-level only in Phase 3 (screens: Phase 10).

    A removal that would take stock below zero is refused unless the shop allows negative stock.
    """
    product = _lock_product(session, ctx.shop_id, product_id)
    _require_active(product)
    shop = get_shop(session, ctx.shop_id)

    if quantity_delta == 0:
        raise InvalidInputError("The adjustment quantity cannot be zero.", field="quantity_delta")
    validate_quantity_for_unit(abs(quantity_delta), _unit_of(session, product), field="quantity_delta")
    if reason_code is AdjustmentReason.OTHER and not (note and note.strip()):
        raise InvalidInputError("Please describe the reason.", field="note")
    when = _check_date(txn_date, shop)

    if quantity_delta < 0 and not shop.allow_negative_stock:
        available = get_stock(session, ctx.shop_id, product.id)
        if available + quantity_delta < 0:
            raise ConflictError(
                f"Not enough stock: {available} available, cannot remove {abs(quantity_delta)}.",
                field="quantity_delta",
            )

    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.ADJUSTMENT,
        qty_delta=quantity_delta,
        txn_date=when,
        reason_code=reason_code,
        note=note.strip() if note else None,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)
    return row


def _flush_or_conflict(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:  # a concurrent duplicate that slipped past the checks above
        raise ConflictError("This stock entry conflicts with an existing one.") from exc
