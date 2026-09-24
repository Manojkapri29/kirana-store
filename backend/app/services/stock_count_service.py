"""Stock counting (cycle counting).

    CREATE (choose scope)  ->  COUNTING (enter quantities)  ->  REVIEW (differences are computed and shown)
        ->  APPROVE (a different person confirms them; a large variance needs a separate approval_request)
        ->  POST (inventory_service writes the adjustments)  ->  the count is done

A count can be CANCELLED at any point before POSTED, with no stock effect. The only step that ever touches the
inventory ledger is `post`, and it does so through `inventory_service.record_adjustment` exactly like any
other adjustment — one `ADJUSTMENT` row per product whose count differed, reason `COUNT_CORRECTION`, noting
which count it came from. Nothing here keeps a second copy of "current stock": `expected_quantity` is a
snapshot taken once, at creation, for the count to explain its own differences against."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Category, Product, StockCount, StockCountItem, Unit
from app.models.enums import AdjustmentReason, StockCountScope, StockCountStatus
from app.services import approval_service, inventory_service, notification_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0")
APPROVAL_KIND = "STOCK_COUNT_LARGE_VARIANCE"


@dataclass(frozen=True)
class ItemView:
    id: int
    product_id: int
    product_name: str
    sku: str
    unit_code: str
    expected_quantity: Decimal
    counted_quantity: Decimal | None
    variance: Decimal | None
    unit_cost_snapshot: Decimal | None
    variance_value: Decimal | None
    note: str | None


def _get(session: Session, shop_id: int, count_id: int, *, lock: bool = False) -> StockCount:
    query = select(StockCount).where(StockCount.id == count_id, StockCount.shop_id == shop_id)
    count = session.scalar(query.with_for_update() if lock else query)
    if count is None:
        raise NotFoundError("Stock count not found")
    return count


def _require_status(count: StockCount, *allowed: StockCountStatus) -> None:
    if count.status not in allowed:
        raise ConflictError(
            f"This stock count is {count.status.value.lower()}; that is not possible right now."
        )


def _scope_products(
    session: Session,
    shop_id: int,
    scope: StockCountScope,
    category_id: int | None,
    product_ids: list[int] | None,
) -> list[Product]:
    query = select(Product).where(Product.shop_id == shop_id, Product.is_active.is_(True))
    if scope is StockCountScope.CATEGORY:
        if category_id is None:
            raise InvalidInputError("Choose a category.", field="category_id")
        category = session.scalar(
            select(Category).where(Category.id == category_id, Category.shop_id == shop_id)
        )
        if category is None:
            raise NotFoundError("Category not found")
        query = query.where(Product.category_id == category_id)
    elif scope is StockCountScope.PRODUCTS:
        if not product_ids:
            raise InvalidInputError("Choose at least one product.", field="product_ids")
        query = query.where(Product.id.in_(product_ids))
    products = list(session.scalars(query))
    if scope is StockCountScope.PRODUCTS and len(products) != len(set(product_ids or [])):
        found = {p.id for p in products}
        missing = [pid for pid in (product_ids or []) if pid not in found]
        raise NotFoundError(f"Product not found: {missing[0]}" if missing else "Product not found")
    if not products:
        raise InvalidInputError("There are no active products in that scope.", field="scope")
    return products


def create(
    session: Session,
    ctx: RequestContext,
    *,
    title: str,
    scope: StockCountScope,
    category_id: int | None = None,
    product_ids: list[int] | None = None,
    notes: str | None = None,
) -> StockCount:
    if not title.strip():
        raise InvalidInputError("Give the count a title.", field="title")
    products = _scope_products(session, ctx.shop_id, scope, category_id, product_ids)
    stock = inventory_service.get_stock_map(session, ctx.shop_id, [p.id for p in products])
    count = StockCount(
        shop_id=ctx.shop_id, title=title.strip(), scope=scope,
        category_id=category_id if scope is StockCountScope.CATEGORY else None,
        notes=notes.strip() if notes else None, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(count)
    session.flush()
    session.add_all(
        StockCountItem(
            shop_id=ctx.shop_id,
            stock_count_id=count.id,
            product_id=p.id,
            expected_quantity=stock.get(p.id, ZERO),
        )
        for p in products
    )
    record_audit(
        session,
        ctx,
        entity_type="stock_count",
        entity_id=count.id,
        action="stock_count_created",
        after={"scope": scope.value, "products": len(products)},
    )
    return count


def start_counting(session: Session, ctx: RequestContext, count_id: int) -> StockCount:
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(count, StockCountStatus.DRAFT)
    count.status = StockCountStatus.COUNTING
    record_audit(
        session, ctx, entity_type="stock_count", entity_id=count.id, action="stock_count_counting_started"
    )
    return count


def enter_counts(session: Session, ctx: RequestContext, count_id: int, entries: list[dict]) -> StockCount:
    """`entries`: [{"product_id": int, "counted_quantity": Decimal, "note": str | None}, ...]. Any number of
    items at once; entering a product again simply replaces its earlier counted quantity (nothing is posted
    until much later)."""
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(count, StockCountStatus.COUNTING)
    items = {
        i.product_id: i
        for i in session.scalars(select(StockCountItem).where(StockCountItem.stock_count_id == count.id))
    }
    units = {
        p.id: u.allows_decimal
        for p, u in session.execute(
            select(Product, Unit).join(Unit, Unit.id == Product.unit_id).where(Product.shop_id == ctx.shop_id)
        )
    }
    now = utc_now()
    for entry in entries:
        product_id = entry.get("product_id")
        item = items.get(product_id)
        if item is None:
            raise InvalidInputError(f"Product {product_id} is not part of this count.", field="product_id")
        try:
            quantity = Decimal(str(entry["counted_quantity"]))
        except Exception as exc:  # noqa: BLE001
            raise InvalidInputError("Enter a valid quantity.", field="counted_quantity") from exc
        if quantity < 0:
            raise InvalidInputError("A counted quantity cannot be negative.", field="counted_quantity")
        if not units.get(product_id, True) and quantity != quantity.to_integral_value():
            raise InvalidInputError(
                "This product's unit does not allow a fractional quantity.", field="counted_quantity"
            )
        item.counted_quantity = quantity
        item.note = (entry.get("note") or "").strip() or None
        item.counted_by = ctx.user_id
        item.counted_at = now
    return count


def submit_for_review(session: Session, ctx: RequestContext, count_id: int) -> StockCount:
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(count, StockCountStatus.COUNTING)
    items = list(session.scalars(select(StockCountItem).where(StockCountItem.stock_count_id == count.id)))
    remaining = [i for i in items if i.counted_quantity is None]
    if remaining:
        raise ConflictError(f"{len(remaining)} product(s) have not been counted yet.")
    products = {
        p.id: p
        for p in session.scalars(
            select(Product).where(
                Product.shop_id == ctx.shop_id, Product.id.in_([i.product_id for i in items])
            )
        )
    }
    total_value = ZERO
    known_all = True
    for item in items:
        item.variance = item.counted_quantity - item.expected_quantity
        product = products[item.product_id]
        item.unit_cost_snapshot = product.avg_cost
        if item.variance != 0:
            if product.avg_cost is not None:
                total_value += abs(item.variance) * product.avg_cost
            else:
                known_all = False
    count.status = StockCountStatus.REVIEW
    count.reviewed_by = ctx.user_id
    count.reviewed_at = utc_now()
    changed = [i for i in items if i.variance != 0]
    if changed:
        notification_service.emit_safely(
            session, ctx.shop_id, "STOCK_COUNT_VARIANCE",
            title="Stock count has differences",
            message=f"'{count.title}' found differences in {len(changed)} product(s).",
            dedupe_key=f"stock_count:{count.id}:review", entity_type="stock_count", entity_id=count.id,
        )  # fmt: skip
    record_audit(
        session, ctx, entity_type="stock_count", entity_id=count.id,
        action="stock_count_submitted_for_review",
        after={
            "differences": len(changed), "variance_value_known": known_all,
            "variance_value": str(total_value),
        },
    )  # fmt: skip
    return count


def approve(session: Session, ctx: RequestContext, count_id: int, *, note: str | None = None) -> StockCount:
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(count, StockCountStatus.REVIEW)
    if count.created_by == ctx.user_id:
        raise ForbiddenError(
            "The person who created this count cannot also approve it.", code="cannot_self_approve"
        )
    shop = get_shop(session, ctx.shop_id)
    items = list(session.scalars(select(StockCountItem).where(StockCountItem.stock_count_id == count.id)))
    observed = sum(
        (
            abs(i.variance) * i.unit_cost_snapshot
            for i in items
            if i.variance and i.unit_cost_snapshot is not None
        ),
        ZERO,
    )
    if shop.stock_count_variance_threshold is not None and observed >= shop.stock_count_variance_threshold:
        approval_service.create(
            session, ctx, kind=APPROVAL_KIND, entity_type="stock_count", entity_id=count.id,
            reason=f"Variance of at least {observed} reaches the shop's approval threshold.",
            threshold_value=shop.stock_count_variance_threshold, observed_value=observed,
        )  # fmt: skip
        count.requires_approval = True
        record_audit(
            session,
            ctx,
            entity_type="stock_count",
            entity_id=count.id,
            action="stock_count_approval_requested",
            after={"observed_value": str(observed)},
        )
        return count
    count.status = StockCountStatus.APPROVED
    count.approved_by = ctx.user_id
    count.approved_at = utc_now()
    record_audit(
        session,
        ctx,
        entity_type="stock_count",
        entity_id=count.id,
        action="stock_count_approved",
        after={"note": note},
    )
    return count


def apply_approval_decision(
    session: Session, ctx: RequestContext, count_id: int, *, approved: bool
) -> StockCount:
    """Called after a large-variance `approval_request` for this count has been decided (see
    `approval_service`)."""
    count = _get(session, ctx.shop_id, count_id, lock=True)
    if approved:
        count.status = StockCountStatus.APPROVED
        count.approved_by = ctx.user_id
        count.approved_at = utc_now()
    else:
        count.status = (
            StockCountStatus.COUNTING
        )  # back to counting: the difference needs a recount, not a repost
        count.requires_approval = False
    record_audit(
        session,
        ctx,
        entity_type="stock_count",
        entity_id=count.id,
        action="stock_count_approval_decided",
        after={"approved": approved},
    )
    return count


def post(session: Session, ctx: RequestContext, count_id: int) -> StockCount:
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(count, StockCountStatus.APPROVED)
    shop = get_shop(session, ctx.shop_id)
    today = shop_today(shop)
    items = list(session.scalars(select(StockCountItem).where(StockCountItem.stock_count_id == count.id)))
    for item in items:
        if item.variance:
            inventory_service.record_adjustment(
                session, ctx, product_id=item.product_id, quantity_delta=item.variance,
                reason_code=AdjustmentReason.COUNT_CORRECTION,
                note=f"Stock count #{count.id} ({count.title})" + (f": {item.note}" if item.note else ""),
                txn_date=today,
            )  # fmt: skip
    count.status = StockCountStatus.POSTED
    count.posted_by = ctx.user_id
    count.posted_at = utc_now()
    record_audit(
        session,
        ctx,
        entity_type="stock_count",
        entity_id=count.id,
        action="stock_count_posted",
        after={"adjusted_products": sum(1 for i in items if i.variance)},
    )
    return count


def cancel(session: Session, ctx: RequestContext, count_id: int, *, reason: str) -> StockCount:
    if not reason.strip():
        raise InvalidInputError("Give a reason for cancelling.", field="reason")
    count = _get(session, ctx.shop_id, count_id, lock=True)
    _require_status(
        count,
        StockCountStatus.DRAFT,
        StockCountStatus.COUNTING,
        StockCountStatus.REVIEW,
        StockCountStatus.APPROVED,
    )
    count.status = StockCountStatus.CANCELLED
    count.cancelled_by = ctx.user_id
    count.cancelled_at = utc_now()
    count.cancel_reason = reason.strip()
    record_audit(
        session,
        ctx,
        entity_type="stock_count",
        entity_id=count.id,
        action="stock_count_cancelled",
        after={"reason": reason.strip()},
    )
    return count


def get(session: Session, shop_id: int, count_id: int) -> StockCount:
    return _get(session, shop_id, count_id)


def list_counts(
    session: Session,
    shop_id: int,
    *,
    status: StockCountStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[StockCount], int]:
    query = select(StockCount).where(StockCount.shop_id == shop_id)
    count_query = select(func.count()).select_from(StockCount).where(StockCount.shop_id == shop_id)
    if status is not None:
        query = query.where(StockCount.status == status)
        count_query = count_query.where(StockCount.status == status)
    total = session.scalar(count_query) or 0
    rows = list(session.scalars(query.order_by(StockCount.id.desc()).limit(limit).offset(offset)))
    return rows, total


def get_items(session: Session, shop_id: int, count_id: int) -> list[ItemView]:
    count = _get(session, shop_id, count_id)
    rows = session.execute(
        select(StockCountItem, Product.name, Product.sku, Unit.code)
        .join(
            Product, (Product.shop_id == StockCountItem.shop_id) & (Product.id == StockCountItem.product_id)
        )
        .join(Unit, Unit.id == Product.unit_id)
        .where(StockCountItem.stock_count_id == count.id)
        .order_by(Product.name)
    ).all()
    out = []
    for item, name, sku, unit_code in rows:
        value = (
            abs(item.variance) * item.unit_cost_snapshot
            if item.variance is not None and item.unit_cost_snapshot is not None
            else None
        )
        out.append(
            ItemView(
                item.id,
                item.product_id,
                name,
                sku,
                unit_code,
                item.expected_quantity,
                item.counted_quantity,
                item.variance,
                item.unit_cost_snapshot,
                value,
                item.note,
            )
        )
    return out
