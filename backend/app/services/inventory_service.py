"""inventory_service: the ONLY module allowed to write to (or read) `inventory_transactions`.

Why one module: stock is derived from the ledger, so every rule about it (signs, no negative stock, one
transaction per business action, insert-only history) lives here where it can be tested thoroughly.
`tests/test_architecture.py` fails if any other module touches the ledger table.

Phase 3 implements:
    - opening stock (the starting balance of a product)
    - stock adjustments (service level only; the screens and API for them arrive in Phase 10)
    - stock queries: current stock, stock for many products, the inventory list, transaction history
    - the stock status (in stock / low stock / out of stock)
Phase 5 adds purchases: receiving stock at a cost (`receive_purchase_line`), undoing it when a purchase
is voided (`reverse_lines`), and the moving weighted average cost that goes with both.
Phase 7 adds sales: checking availability (`find_shortages`) and taking stock out for a sale line
(`issue_sale_line`), which also reports the cost of the goods sold. Returns are added in later phases
through this same module.

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
from app.db.types import from_thousandths, paise_to_rupees, round_money, rupees_to_paise, to_thousandths
from app.models import (
    Category,
    InventoryTransaction,
    Product,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Shop,
    Unit,
    User,
)
from app.models.enums import AdjustmentReason, InventoryTxnType, StockReferenceType
from app.services import costing_service
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
    purchase_id: int | None = None  # the purchase this row came from (a purchase line or its reversal)
    purchase_no: str | None = None
    sale_id: int | None = None  # the sale this row came from (a sale line or its reversal)
    sale_no: str | None = None


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
            Purchase.id.label("purchase_id"),
            Purchase.purchase_no.label("purchase_no"),
            Sale.id.label("sale_id"),
            Sale.invoice_no.label("sale_no"),
        )
        .join(Product, and_(Product.shop_id == t.shop_id, Product.id == t.product_id))
        .join(User, and_(User.shop_id == t.shop_id, User.id == t.created_by))
        # Rows that came from a purchase line say which purchase, so screens can link to it.
        .outerjoin(
            PurchaseItem,
            and_(
                t.reference_type == StockReferenceType.PURCHASE_ITEM,
                PurchaseItem.shop_id == t.shop_id,
                PurchaseItem.id == t.reference_id,
            ),
        )
        .outerjoin(
            Purchase, and_(Purchase.shop_id == PurchaseItem.shop_id, Purchase.id == PurchaseItem.purchase_id)
        )
        # Rows that came from a sale line say which sale, so screens can link to it.
        .outerjoin(
            SaleItem,
            and_(
                t.reference_type == StockReferenceType.SALE_ITEM,
                SaleItem.shop_id == t.shop_id,
                SaleItem.id == t.reference_id,
            ),
        )
        .outerjoin(Sale, and_(Sale.shop_id == SaleItem.shop_id, Sale.id == SaleItem.sale_id))
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
        raise ConflictError(
            "This stock entry conflicts with an existing one.", code="duplicate_record"
        ) from exc


# --- Purchases: receiving stock and its cost -------------------------------------------------------


def lock_products(session: Session, shop_id: int, product_ids: Sequence[int]) -> dict[int, Product]:
    """Lock several products of this shop at once, always in ascending id order.

    A fixed order means two transactions that touch the same products can never wait on each other in a
    circle (a deadlock on PostgreSQL). A product of another shop, or one that does not exist, is "not
    found".
    """
    locked: dict[int, Product] = {}
    for product_id in sorted(set(product_ids)):
        locked[product_id] = _lock_product(session, shop_id, product_id)
    return locked


@dataclass(frozen=True)
class PurchaseReceipt:
    """What receiving one purchase line did, for the snapshot stored on the line."""

    transaction: InventoryTransaction
    stock_before: Decimal
    avg_cost_before: Decimal | None
    avg_cost_after: Decimal | None


def receive_purchase_line(
    session: Session,
    ctx: RequestContext,
    *,
    product: Product,
    quantity: Decimal,
    line_total: Decimal,
    purchase_item_id: int,
    txn_date: date,
) -> PurchaseReceipt:
    """Add a purchased quantity to stock (one PURCHASE ledger row) and update the average cost.

    `product` must already be locked (see `lock_products`). `line_total` is the net cost of the line, after
    any discount. The average cost is recalculated from it (see `costing_service`); the ledger row records
    the net cost per unit. Stock and cost change in the caller's transaction, so they succeed or fail
    together.
    """
    if product.shop_id != ctx.shop_id:  # defence in depth: lock_products already scopes by shop
        raise NotFoundError("Product not found")
    _require_active(product)
    if quantity <= 0:
        raise InvalidInputError("The quantity must be greater than zero.", field="quantity")

    stock_before = get_stock(session, ctx.shop_id, product.id)
    avg_before = product.avg_cost
    avg_after = costing_service.next_average_cost(
        stock_before=stock_before, avg_before=avg_before, quantity=quantity, line_value=line_total
    )

    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.PURCHASE,
        qty_delta=quantity,
        unit_cost=round_money(line_total / quantity),  # net cost per unit; costing itself uses line_total
        txn_date=txn_date,
        reference_type=StockReferenceType.PURCHASE_ITEM,
        reference_id=purchase_item_id,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)
    product.avg_cost = avg_after
    return PurchaseReceipt(row, stock_before, avg_before, avg_after)


def rebuild_average_cost(session: Session, shop_id: int, product_id: int) -> Decimal | None:
    """Recompute a product's average cost from its whole ledger and store it. Returns the new average.

    Movements that were later reversed are left out together with their reversal, so a voided purchase
    leaves no trace. The product must be locked by the caller. The replayed stock must equal the ledger
    stock; if it does not, something is wrong and nothing is guessed.
    """
    t = InventoryTransaction
    rows = session.execute(
        select(
            t.id, t.txn_type, t.qty_delta, t.unit_cost, t.reverses_txn_id, t.reference_type, t.reference_id
        )
        .where(t.shop_id == shop_id, t.product_id == product_id)
        .order_by(t.id)  # the order things were recorded in, which is the order the live update used
    ).all()
    cancelled = {row.reverses_txn_id for row in rows if row.reverses_txn_id is not None}
    live = [r for r in rows if r.id not in cancelled and r.txn_type is not InventoryTxnType.REVERSAL]

    purchase_item_ids = [r.reference_id for r in live if r.reference_type is StockReferenceType.PURCHASE_ITEM]
    line_totals = (
        dict(
            session.execute(
                select(PurchaseItem.id, PurchaseItem.line_total).where(
                    PurchaseItem.shop_id == shop_id, PurchaseItem.id.in_(purchase_item_ids)
                )
            ).all()
        )
        if purchase_item_ids
        else {}
    )

    events = [
        costing_service.CostEvent(
            txn_type=r.txn_type,
            quantity_delta=r.qty_delta,
            unit_cost=r.unit_cost,
            line_value=line_totals.get(r.reference_id) if r.txn_type is InventoryTxnType.PURCHASE else None,
        )
        for r in live
    ]
    replayed_stock, average = costing_service.replay_average_cost(events)
    actual_stock = get_stock(session, shop_id, product_id)
    if replayed_stock != actual_stock:
        raise RuntimeError(
            f"Stock replay mismatch for product {product_id}: ledger says {actual_stock}, replay says "
            f"{replayed_stock}. The average cost was not changed."
        )
    product = session.scalars(
        select(Product).where(Product.shop_id == shop_id, Product.id == product_id)
    ).one()
    product.avg_cost = average
    return average


def reverse_lines(
    session: Session,
    ctx: RequestContext,
    *,
    reference_type: StockReferenceType,
    reference_ids: Sequence[int],
    note: str,
) -> list[InventoryTransaction]:
    """Undo the stock effect of document lines (used when a purchase is voided).

    For every ledger row that came from one of the lines, an equal and opposite REVERSAL row is added. The
    original rows are never touched. Reversing removes stock, so it is refused if that stock has already
    gone (unless the shop allows negative stock). Afterwards each affected product's average cost is
    rebuilt from its history without the cancelled movements. Everything happens in the caller's
    transaction.
    """
    t = InventoryTransaction
    originals = list(
        session.scalars(
            select(t)
            .where(
                t.shop_id == ctx.shop_id,
                t.reference_type == reference_type,
                t.reference_id.in_(reference_ids),
            )
            .where(t.txn_type != InventoryTxnType.REVERSAL)
            .order_by(t.id)
        )
    )
    if not originals:
        return []

    already = set(
        session.scalars(
            select(t.reverses_txn_id).where(
                t.shop_id == ctx.shop_id, t.reverses_txn_id.in_([o.id for o in originals])
            )
        )
    )
    if already:
        raise ConflictError("This has already been reversed.", code="already_done")

    products = lock_products(session, ctx.shop_id, [o.product_id for o in originals])
    shop = get_shop(session, ctx.shop_id)

    removed: dict[int, Decimal] = {}
    for original in originals:
        removed[original.product_id] = removed.get(original.product_id, ZERO) + original.qty_delta
    if not shop.allow_negative_stock:
        for product_id, quantity in removed.items():
            available = get_stock(session, ctx.shop_id, product_id)
            if quantity > 0 and available - quantity < 0:
                raise ConflictError(
                    f"Cannot reverse: '{products[product_id].name}' has only {available} in stock, but "
                    f"{quantity} of it would be removed. Some of it has already been sold or removed.",
                    code="inventory_conflict",
                )

    today = shop_today(shop)
    reversals: list[InventoryTransaction] = []
    for original in originals:
        row = InventoryTransaction(
            shop_id=ctx.shop_id,
            product_id=original.product_id,
            txn_type=InventoryTxnType.REVERSAL,
            qty_delta=-original.qty_delta,
            unit_cost=original.unit_cost,
            txn_date=today,
            reference_type=original.reference_type,
            reference_id=original.reference_id,
            reverses_txn_id=original.id,
            note=note,
            created_by=ctx.user_id,
        )
        session.add(row)
        reversals.append(row)
    _flush_or_conflict(session)

    for product_id in removed:
        rebuild_average_cost(session, ctx.shop_id, product_id)
    return reversals


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    txn_type: InventoryTxnType
    qty_delta: Decimal
    txn_date: date


def entries_for_lines(
    session: Session, shop_id: int, reference_type: StockReferenceType, reference_ids: Sequence[int]
) -> dict[int, list[LedgerEntry]]:
    """The ledger rows behind document lines, by line id: the "inventory effect" of a purchase."""
    t = InventoryTransaction
    found: dict[int, list[LedgerEntry]] = {reference_id: [] for reference_id in reference_ids}
    if not reference_ids:
        return found
    rows = session.execute(
        select(t.reference_id, t.id, t.txn_type, t.qty_delta, t.txn_date)
        .where(t.shop_id == shop_id, t.reference_type == reference_type, t.reference_id.in_(reference_ids))
        .order_by(t.id)
    )
    for reference_id, txn_id, txn_type, qty_delta, txn_date in rows:
        found[reference_id].append(LedgerEntry(txn_id, txn_type, qty_delta, txn_date))
    return found


# --- Sales: taking stock out ---------------------------------------------------------------------


@dataclass(frozen=True)
class StockShortage:
    """A product a sale wants more of than the shelf has."""

    product_id: int
    name: str
    unit_code: str
    available: Decimal
    requested: Decimal


def find_shortages(session: Session, shop_id: int, requested: dict[int, Decimal]) -> list[StockShortage]:
    """Products whose requested quantity is more than the stock on hand. `requested` is the TOTAL per
    product
    (a product on two lines counts once). Empty when the shop allows negative stock (L8).

    This is a check, not a lock: `issue_sale_line` repeats it under the product's lock.
    """
    if not requested or get_shop(session, shop_id).allow_negative_stock:
        return []
    stock = get_stock_map(session, shop_id, list(requested))
    rows = session.execute(
        select(Product.id, Product.name, Unit.code)
        .join(Unit, Unit.id == Product.unit_id)
        .where(Product.shop_id == shop_id, Product.id.in_(list(requested)))
    ).all()
    return [
        StockShortage(pid, name, code, stock[pid], requested[pid])
        for pid, name, code in rows
        if requested[pid] > stock[pid]
    ]


def shortage_message(shortage: StockShortage) -> str:
    return (
        f"Not enough stock of '{shortage.name}': {shortage.available} {shortage.unit_code} available, "
        f"{shortage.requested} {shortage.unit_code} needed."
    )


@dataclass(frozen=True)
class SaleIssue:
    """What taking one sale line out of stock did, for the snapshot stored on the line."""

    transaction: InventoryTransaction
    unit_cost: Decimal | None  # the product's average cost at the moment of sale; None = unknown
    cogs: Decimal | None  # quantity x unit_cost; None = unknown


def issue_sale_line(
    session: Session,
    ctx: RequestContext,
    *,
    product: Product,
    quantity: Decimal,
    sale_item_id: int,
    txn_date: date,
) -> SaleIssue:
    """Take a sold quantity out of stock (one SALE ledger row) and report its cost.

    `product` must already be locked (see `lock_products`). The stock is read again here, under that lock,
    so two simultaneous sales cannot both take the last unit. Unless the shop allows negative stock, a sale
    of more than is on hand is refused and nothing is written. The cost is the product's current weighted
    average (maintained by purchases, never recomputed here); a sale does not change it. An unknown cost
    stays unknown.
    """
    if product.shop_id != ctx.shop_id:  # defence in depth: lock_products already scopes by shop
        raise NotFoundError("Product not found")
    _require_active(product)
    if quantity <= 0:
        raise InvalidInputError("The quantity must be greater than zero.", field="quantity")

    available = get_stock(session, ctx.shop_id, product.id)
    if quantity > available and not get_shop(session, ctx.shop_id).allow_negative_stock:
        unit = _unit_of(session, product)
        raise ConflictError(
            shortage_message(StockShortage(product.id, product.name, unit.code, available, quantity)),
            field="quantity",
            code="insufficient_stock",
        )

    unit_cost = product.avg_cost
    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.SALE,
        qty_delta=-quantity,
        unit_cost=unit_cost,
        txn_date=txn_date,
        reference_type=StockReferenceType.SALE_ITEM,
        reference_id=sale_item_id,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)
    return SaleIssue(row, unit_cost, costing_service.cost_of_goods(quantity, unit_cost))


# --- Returns ---------------------------------------------------------------------------------------


def receive_sale_return_line(
    session: Session,
    ctx: RequestContext,
    *,
    product: Product,
    quantity: Decimal,
    sales_return_item_id: int,
    unit_cost: Decimal | None,
    txn_date: date,
) -> InventoryTransaction:
    """Put returned goods back on the shelf (one SALE_RETURN ledger row) at the cost of the line they were
    sold
    from, then rebuild the product's average cost. `product` must already be locked. An unknown cost stays
    unknown. The product may since have been deactivated: a customer can still bring it back."""
    if product.shop_id != ctx.shop_id:
        raise NotFoundError("Product not found")
    if quantity <= 0:
        raise InvalidInputError("The quantity must be greater than zero.", field="quantity")
    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.SALE_RETURN,
        qty_delta=quantity,
        unit_cost=unit_cost,
        txn_date=txn_date,
        reference_type=StockReferenceType.SALES_RETURN_ITEM,
        reference_id=sales_return_item_id,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)
    rebuild_average_cost(session, ctx.shop_id, product.id)
    return row


def issue_purchase_return_line(
    session: Session,
    ctx: RequestContext,
    *,
    product: Product,
    quantity: Decimal,
    purchase_return_item_id: int,
    unit_cost: Decimal,
    txn_date: date,
) -> InventoryTransaction:
    """Send goods back to a supplier (one PURCHASE_RETURN ledger row) at the cost they came in at.

    `product` must already be locked. The stock is read again here, under that lock: goods that were already
    sold cannot be sent back (BUSINESS_RULES R3), unless the shop allows negative stock."""
    if product.shop_id != ctx.shop_id:
        raise NotFoundError("Product not found")
    if quantity <= 0:
        raise InvalidInputError("The quantity must be greater than zero.", field="quantity")
    available = get_stock(session, ctx.shop_id, product.id)
    if quantity > available and not get_shop(session, ctx.shop_id).allow_negative_stock:
        unit = _unit_of(session, product)
        raise ConflictError(
            shortage_message(StockShortage(product.id, product.name, unit.code, available, quantity)),
            field="quantity",
            code="insufficient_stock",
        )
    row = InventoryTransaction(
        shop_id=ctx.shop_id,
        product_id=product.id,
        txn_type=InventoryTxnType.PURCHASE_RETURN,
        qty_delta=-quantity,
        unit_cost=unit_cost,
        txn_date=txn_date,
        reference_type=StockReferenceType.PURCHASE_RETURN_ITEM,
        reference_id=purchase_return_item_id,
        created_by=ctx.user_id,
    )
    session.add(row)
    _flush_or_conflict(session)
    return row


# --- Read-only view of adjustments (for the AI insights) ---------------------------------------------


@dataclass(frozen=True)
class AdjustmentEntry:
    txn_id: int
    product_id: int
    product_name: str
    unit_code: str
    quantity_delta: Decimal
    stock_before: Decimal
    reason_code: str | None
    day: date


def adjustments_between(session: Session, shop_id: int, start: date, end: date) -> list[AdjustmentEntry]:
    """Stock adjustments dated in the period, with the stock each one started from, newest first."""
    rows = session.execute(
        select(InventoryTransaction, Product.name, Unit.code)
        .join(
            Product,
            (Product.shop_id == InventoryTransaction.shop_id)
            & (Product.id == InventoryTransaction.product_id),
        )
        .join(Unit, Unit.id == Product.unit_id)
        .where(
            InventoryTransaction.shop_id == shop_id,
            InventoryTransaction.txn_type == InventoryTxnType.ADJUSTMENT,
            InventoryTransaction.txn_date >= start,
            InventoryTransaction.txn_date <= end,
        )
        .order_by(InventoryTransaction.txn_date.desc(), InventoryTransaction.id.desc())
    ).all()
    out: list[AdjustmentEntry] = []
    for txn, name, unit_code in rows:
        before = session.scalar(
            select(func.coalesce(func.sum(InventoryTransaction.qty_delta), 0)).where(
                InventoryTransaction.shop_id == shop_id,
                InventoryTransaction.product_id == txn.product_id,
                InventoryTransaction.id < txn.id,
            )
        )
        out.append(
            AdjustmentEntry(
                txn.id,
                txn.product_id,
                name,
                unit_code,
                txn.qty_delta,
                before if isinstance(before, Decimal) else Decimal(str(before or 0)),
                txn.reason_code.value if txn.reason_code else None,
                txn.txn_date,
            )
        )
    return out
