"""The online store: settings, the product listing, public ordering and the staff order workflow.

An order is a request. Placing one changes no stock and no money. The shop accepts it and moves it along, and when it is DELIVERED the ordinary
Detailed Sale is created and posted through `sale_service` in the same transaction, so stock, cost, khata, loyalty, returns, reports and finance
behave exactly as for a counter sale (BUSINESS_RULES O2). Nothing is charged online: the customer pays cash or UPI by hand.

Public functions take a store `slug` and never a session user, so they are written to reveal the least possible: only listed, active products;
price, unit and whether the product is in stock (never the quantity, the cost or anyone's data); an order is visible only to whoever holds
its tracking token (only the token's SHA-256 is stored). Every miss is the same "not found".
"""

import hashlib
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import (
    Category,
    Customer,
    OnlineOrder,
    OnlineOrderEvent,
    OnlineOrderItem,
    Product,
    StoreListing,
    StoreSettings,
    Unit,
)
from app.models.enums import (
    CustomerSource,
    OnlineOrderFulfilment,
    OnlineOrderPayment,
    OnlineOrderStatus,
    PaymentMethod,
)
from app.services import (
    contact_validation,
    customer_service,
    inventory_service,
    notification_service,
    numbering_service,
    sale_service,
)
from app.services import (
    sale_calculation as calc,
)
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0.00")
S = OnlineOrderStatus
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$")
RESERVED_SLUGS = frozenset({"admin", "api", "app", "assets", "login", "store", "shop", "www", "health", "static", "public"})
KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,100}$")
MAX_LINES = 30
MAX_QUANTITY = Decimal("10000")
MAX_OPEN_ORDERS_PER_PHONE = 5
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# What a status can become, and who may cause it. (Permission checks are the router's; this is the order's own rule.)
TRANSITIONS: dict[OnlineOrderStatus, frozenset[OnlineOrderStatus]] = {
    S.PLACED: frozenset({S.ACCEPTED, S.REJECTED, S.CANCELLED}),
    S.ACCEPTED: frozenset({S.PREPARING, S.READY, S.CANCELLED}),
    S.PREPARING: frozenset({S.READY, S.CANCELLED}),
    S.READY: frozenset({S.OUT_FOR_DELIVERY, S.DELIVERED, S.CANCELLED}),
    S.OUT_FOR_DELIVERY: frozenset({S.DELIVERED, S.CANCELLED}),
    S.DELIVERED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}
OPEN_STATUSES = (S.PLACED, S.ACCEPTED, S.PREPARING, S.READY, S.OUT_FOR_DELIVERY)
EVENT_FOR = {
    S.PLACED: "ONLINE_ORDER_PLACED",
    S.ACCEPTED: "ONLINE_ORDER_ACCEPTED",
    S.REJECTED: "ONLINE_ORDER_REJECTED",
    S.READY: "ONLINE_ORDER_READY",
    S.OUT_FOR_DELIVERY: "ONLINE_ORDER_OUT_FOR_DELIVERY",
    S.DELIVERED: "ONLINE_ORDER_DELIVERED",
}


def _clean(value: str | None, *, field_name: str, minimum: int = 0, maximum: int, required: bool = False) -> str | None:
    text = " ".join(_CONTROL.sub(" ", value or "").split())
    if not text:
        if required:
            raise InvalidInputError("This is required.", field=field_name)
        return None
    if len(text) < minimum or len(text) > maximum:
        raise InvalidInputError(f"Use {minimum} to {maximum} characters.", field=field_name)
    return text


# --- Settings and listing (staff) ---------------------------------------------------------------------------------


def get_store(session: Session, shop_id: int) -> StoreSettings | None:
    return session.scalar(select(StoreSettings).where(StoreSettings.shop_id == shop_id))


def save_store(session: Session, ctx: RequestContext, values: dict[str, Any]) -> StoreSettings:
    """Create or change the store's settings. The slug is the public address: it is required the first time and can be changed later
    (the old address then stops working)."""
    row = get_store(session, ctx.shop_id)
    before = None if row is None else _store_snapshot(row)
    slug = values.get("slug", row.slug if row else None)
    if slug is None:
        raise InvalidInputError("Choose the store's web address.", field="slug")
    slug = str(slug).strip().lower()
    if not SLUG.fullmatch(slug) or "--" in slug or slug in RESERVED_SLUGS:
        raise InvalidInputError("Use 3 to 40 lowercase letters, digits or hyphens (not at the ends).", field="slug")
    name = _clean(values.get("display_name", row.display_name if row else get_shop(session, ctx.shop_id).name), field_name="display_name", minimum=2, maximum=120, required=True)
    phone = contact_validation.normalize_phone(values.get("contact_phone", row.contact_phone if row else None), field="contact_phone")
    announcement = _clean(values.get("announcement", row.announcement if row else None), field_name="announcement", maximum=300)
    merged: dict[str, Any] = {
        key: values.get(key, getattr(row, key) if row else default)
        for key, default in (
            ("is_open", False), ("accepts_cod", True), ("accepts_upi", True), ("delivery_enabled", True), ("pickup_enabled", True),
            ("min_order_amount", ZERO),
        )
    }  # fmt: skip
    if not (merged["accepts_cod"] or merged["accepts_upi"]):
        raise InvalidInputError("Accept at least one way to pay: cash or UPI.", field="accepts_cod")
    if not (merged["delivery_enabled"] or merged["pickup_enabled"]):
        raise InvalidInputError("Offer at least one way to get the order: delivery or pickup.", field="delivery_enabled")
    minimum = Decimal(merged["min_order_amount"])
    if minimum < 0:
        raise InvalidInputError("The minimum order cannot be negative.", field="min_order_amount")
    taken = session.scalar(select(StoreSettings.shop_id).where(StoreSettings.slug == slug))
    if taken is not None and taken != ctx.shop_id:
        raise ConflictError("That web address is already taken. Choose another.", field="slug", code="slug_taken")
    if row is None:
        row = StoreSettings(shop_id=ctx.shop_id, slug=slug, display_name=name)
        session.add(row)
    row.slug, row.display_name, row.contact_phone, row.announcement = slug, name, phone, announcement
    for key in ("is_open", "accepts_cod", "accepts_upi", "delivery_enabled", "pickup_enabled"):
        setattr(row, key, bool(merged[key]))
    row.min_order_amount = minimum
    try:
        session.flush()
    except IntegrityError:
        raise ConflictError("That web address is already taken. Choose another.", field="slug", code="slug_taken") from None
    record_audit(session, ctx, entity_type="online_store", entity_id=row.id, action="update" if before else "create", before=before, after=_store_snapshot(row))
    return row


def _store_snapshot(row: StoreSettings) -> dict[str, Any]:
    return {
        "slug": row.slug, "display_name": row.display_name, "is_open": row.is_open, "accepts_cod": row.accepts_cod,
        "accepts_upi": row.accepts_upi, "delivery_enabled": row.delivery_enabled, "pickup_enabled": row.pickup_enabled,
        "min_order_amount": str(row.min_order_amount),
    }  # fmt: skip


@dataclass(frozen=True)
class ListingRow:
    product_id: int
    sku: str
    name: str
    category: str
    unit: str
    price: Decimal
    is_active: bool
    is_visible: bool


def list_listings(session: Session, shop_id: int, *, q: str | None, only_visible: bool, limit: int, offset: int) -> tuple[list[ListingRow], int]:
    query = (
        select(Product, Unit.code, Category.name, StoreListing.is_visible)
        .join(Unit, Unit.id == Product.unit_id)
        .join(Category, (Category.shop_id == Product.shop_id) & (Category.id == Product.category_id))
        .outerjoin(StoreListing, (StoreListing.shop_id == Product.shop_id) & (StoreListing.product_id == Product.id))
        .where(Product.shop_id == shop_id)
    )
    if q and q.strip():
        like = f"%{q.strip()[:60].lower()}%"
        query = query.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like)))
    if only_visible:
        query = query.where(StoreListing.is_visible.is_(True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(query.order_by(Product.name, Product.id).limit(limit).offset(offset)).all()
    return [ListingRow(p.id, p.sku, p.name, cat, unit, p.selling_price, p.is_active, bool(vis)) for p, unit, cat, vis in rows], total


def set_listing(session: Session, ctx: RequestContext, product_id: int, visible: bool) -> ListingRow:
    product = session.scalar(select(Product).where(Product.shop_id == ctx.shop_id, Product.id == product_id))
    if product is None:
        raise NotFoundError("Product not found.")
    if visible and not product.is_active:
        raise InvalidInputError("An inactive product cannot be shown in the store.", field="visible")
    row = session.scalar(select(StoreListing).where(StoreListing.shop_id == ctx.shop_id, StoreListing.product_id == product_id))
    if row is None:
        row = StoreListing(shop_id=ctx.shop_id, product_id=product_id, is_visible=visible)
        session.add(row)
    else:
        row.is_visible = visible
    session.flush()
    record_audit(session, ctx, entity_type="store_listing", entity_id=row.id, action="show" if visible else "hide", after={"product_id": product_id})
    listings, _ = list_listings(session, ctx.shop_id, q=product.sku, only_visible=False, limit=5, offset=0)
    return next(r for r in listings if r.product_id == product_id)


# --- The public storefront -----------------------------------------------------------------------------------------


def _public_store(session: Session, slug: str) -> StoreSettings:
    row = session.scalar(select(StoreSettings).where(StoreSettings.slug == (slug or "").strip().lower()))
    if row is None:
        raise NotFoundError("This store was not found.")
    return row


def public_store(session: Session, slug: str) -> StoreSettings:
    return _public_store(session, slug)


@dataclass(frozen=True)
class PublicProduct:
    id: int
    name: str
    category: str
    unit: str
    allows_decimal: bool
    price: Decimal
    in_stock: bool


def public_products(session: Session, slug: str, *, q: str | None, category: str | None, limit: int, offset: int) -> tuple[list[PublicProduct], int]:
    store = _public_store(session, slug)
    query = (
        select(Product, Unit, Category.name)
        .join(StoreListing, (StoreListing.shop_id == Product.shop_id) & (StoreListing.product_id == Product.id))
        .join(Unit, Unit.id == Product.unit_id)
        .join(Category, (Category.shop_id == Product.shop_id) & (Category.id == Product.category_id))
        .where(Product.shop_id == store.shop_id, Product.is_active.is_(True), StoreListing.is_visible.is_(True))
    )
    if q and q.strip():
        query = query.where(func.lower(Product.name).like(f"%{q.strip()[:60].lower()}%"))
    if category and category.strip():
        query = query.where(func.lower(Category.name) == category.strip()[:60].lower())
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(query.order_by(Product.name, Product.id).limit(limit).offset(offset)).all()
    stock = inventory_service.get_stock_map(session, store.shop_id, [p.id for p, _, _ in rows])
    allow_negative = get_shop(session, store.shop_id).allow_negative_stock
    return [PublicProduct(p.id, p.name, cat, unit.code, unit.allows_decimal, p.selling_price, allow_negative or stock[p.id] > 0) for p, unit, cat in rows], total


@dataclass
class OrderLineIn:
    product_id: int
    quantity: Decimal


@dataclass
class OrderIn:
    customer_name: str
    customer_phone: str
    fulfilment: OnlineOrderFulfilment
    payment: OnlineOrderPayment
    items: Sequence[OrderLineIn]
    delivery_address: str | None = None
    notes: str | None = None


def _tracking_token(shop_id: int, key: str) -> str:
    """A token derived from the request key, so a repeated request gets the same token back (only its hash is stored)."""
    secret = get_settings().secret_key
    pepper = secret.get_secret_value() if secret else "kirana-development-tracking"
    return hmac.new(pepper.encode(), f"track:{shop_id}:{key}".encode(), hashlib.sha256).hexdigest()[:32]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _fingerprint(order: OrderIn, lines: list[tuple[int, Decimal]]) -> str:
    parts = [order.customer_name, order.customer_phone, order.fulfilment.value, order.payment.value, order.delivery_address or "", order.notes or ""]
    parts += [f"{pid}:{qty}" for pid, qty in lines]
    return _hash("|".join(parts))


@dataclass
class PlacedOrder:
    order: OnlineOrder
    items: list[OnlineOrderItem]
    tracking_token: str
    replayed: bool = False


def place_order(session: Session, slug: str, order_in: OrderIn, idempotency_key: str) -> PlacedOrder:
    """A customer places an order. Prices come from the products, here, never from the customer. Nothing moves in stock or money."""
    store = _public_store(session, slug)
    if not KEY.fullmatch(idempotency_key or ""):
        raise InvalidInputError("A request key of 8 to 100 letters, digits or - _ . : is required.", field="idempotency_key")
    name = _clean(order_in.customer_name, field_name="customer_name", minimum=2, maximum=120, required=True)
    phone = contact_validation.normalize_phone(order_in.customer_phone, field="customer_phone")
    if phone is None:
        raise InvalidInputError("Enter your phone number.", field="customer_phone")
    address = _clean(order_in.delivery_address, field_name="delivery_address", minimum=5, maximum=500)
    notes = _clean(order_in.notes, field_name="notes", maximum=300)
    merged: dict[int, Decimal] = {}
    for line in order_in.items:
        merged[line.product_id] = merged.get(line.product_id, Decimal("0")) + Decimal(line.quantity)
    lines = sorted(merged.items())
    fingerprint = _fingerprint(OrderIn(name, phone, order_in.fulfilment, order_in.payment, [], address, notes), lines)
    existing = session.scalar(select(OnlineOrder).where(OnlineOrder.shop_id == store.shop_id, OnlineOrder.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise ConflictError("This request key was already used for a different order.", code="idempotency_key_reused")
        return PlacedOrder(existing, _items(session, existing), _tracking_token(store.shop_id, idempotency_key), replayed=True)

    if not store.is_open:
        raise ConflictError("This store is not taking orders right now.", code="store_closed")
    if order_in.fulfilment is OnlineOrderFulfilment.DELIVERY and not store.delivery_enabled:
        raise InvalidInputError("This store does not deliver. Choose pickup.", field="fulfilment")
    if order_in.fulfilment is OnlineOrderFulfilment.PICKUP and not store.pickup_enabled:
        raise InvalidInputError("This store does not offer pickup. Choose delivery.", field="fulfilment")
    if order_in.payment is OnlineOrderPayment.COD and not store.accepts_cod:
        raise InvalidInputError("This store does not take cash. Choose UPI.", field="payment")
    if order_in.payment is OnlineOrderPayment.UPI and not store.accepts_upi:
        raise InvalidInputError("This store does not take UPI. Choose cash.", field="payment")
    if order_in.fulfilment is OnlineOrderFulfilment.DELIVERY and address is None:
        raise InvalidInputError("Enter the delivery address.", field="delivery_address")
    if order_in.fulfilment is OnlineOrderFulfilment.PICKUP:
        address = None
    if not lines:
        raise InvalidInputError("Add at least one item.", field="items")
    if len(lines) > MAX_LINES:
        raise InvalidInputError(f"An order can have at most {MAX_LINES} different items.", field="items")

    open_now = session.scalar(
        select(func.count()).select_from(OnlineOrder).where(OnlineOrder.shop_id == store.shop_id, OnlineOrder.customer_phone == phone, OnlineOrder.status == S.PLACED)
    ) or 0
    if open_now >= MAX_OPEN_ORDERS_PER_PHONE:
        raise ConflictError("You already have several orders waiting for the shop to accept. Please wait for them before placing another.", code="too_many_open_orders")

    products = {
        p.id: p
        for p in session.scalars(
            select(Product)
            .join(StoreListing, (StoreListing.shop_id == Product.shop_id) & (StoreListing.product_id == Product.id))
            .where(Product.shop_id == store.shop_id, Product.id.in_([pid for pid, _ in lines]), Product.is_active.is_(True), StoreListing.is_visible.is_(True))
        )
    }
    units = {u.id: u for u in session.scalars(select(Unit))}
    stock = inventory_service.get_stock_map(session, store.shop_id, list(products))
    allow_negative = get_shop(session, store.shop_id).allow_negative_stock
    problems: list[tuple[str | None, str]] = []
    built: list[tuple[Product, Unit, Decimal, Decimal]] = []
    for index, (product_id, quantity) in enumerate(lines):
        where = f"items.{index}.quantity"
        product = products.get(product_id)
        if product is None:
            problems.append((f"items.{index}.product_id", "This product is not available in the store."))
            continue
        unit = units[product.unit_id]
        if quantity <= 0 or quantity > MAX_QUANTITY:
            problems.append((where, "Choose a quantity greater than zero."))
            continue
        try:
            inventory_service.validate_quantity_for_unit(quantity, unit, field=where)
        except InvalidInputError as exc:
            problems.extend((f or where, m) for f, m in exc.errors)
            continue
        if not allow_negative and stock[product.id] < quantity:
            problems.append((where, f"Only {stock[product.id].normalize():f} {unit.code} of {product.name} available."))
            continue
        built.append((product, unit, quantity, calc.line_gross(quantity, product.selling_price)))
    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    total = sum((gross for _, _, _, gross in built), ZERO)
    if total <= 0:
        raise InvalidInputError("The order total must be more than zero.", field="items")
    if total < store.min_order_amount:
        raise InvalidInputError(f"The minimum order is {store.min_order_amount:.2f}.", field="items")

    token = _tracking_token(store.shop_id, idempotency_key)
    fiscal = numbering_service.fiscal_year_label(shop_today(get_shop(session, store.shop_id)))
    number = numbering_service.next_number(session, store.shop_id, "ONLINE_ORDER", fiscal)
    order = OnlineOrder(
        shop_id=store.shop_id, order_no=numbering_service.format_document_number("ORD", fiscal, number), tracking_hash=_hash(token),
        idempotency_key=idempotency_key, request_hash=fingerprint, status=S.PLACED, fulfilment_type=order_in.fulfilment,
        payment_method=order_in.payment, customer_name=name, customer_phone=phone, delivery_address=address, notes=notes,
        total_amount=total, placed_at=utc_now(),
    )  # fmt: skip
    try:
        with session.begin_nested():
            session.add(order)
            session.flush()
    except IntegrityError:  # the same request arrived twice at once: the other one won, so answer with it
        won = session.scalar(select(OnlineOrder).where(OnlineOrder.shop_id == store.shop_id, OnlineOrder.idempotency_key == idempotency_key))
        if won is None:
            raise
        return PlacedOrder(won, _items(session, won), token, replayed=True)
    items = []
    for product, unit, quantity, gross in built:
        item = OnlineOrderItem(shop_id=store.shop_id, order_id=order.id, product_id=product.id, product_name=product.name, unit_label=unit.code, quantity=quantity, unit_price=product.selling_price, line_total=gross)
        session.add(item)
        items.append(item)
    session.add(OnlineOrderEvent(shop_id=store.shop_id, order_id=order.id, from_status=None, to_status=S.PLACED.value, actor="CUSTOMER"))
    session.flush()
    notification_service.emit_safely(
        session, store.shop_id, "ONLINE_ORDER_PLACED", title="New online order",
        message=f"{order.order_no}: {len(items)} item(s), {total:.2f}. Open Online orders to accept it.",
        dedupe_key=f"online-order-{order.id}-placed", entity_type="online_order", entity_id=order.id,
    )  # fmt: skip
    return PlacedOrder(order, items, token)


def _items(session: Session, order: OnlineOrder) -> list[OnlineOrderItem]:
    return list(session.scalars(select(OnlineOrderItem).where(OnlineOrderItem.shop_id == order.shop_id, OnlineOrderItem.order_id == order.id).order_by(OnlineOrderItem.id)))


def _events(session: Session, order: OnlineOrder) -> list[OnlineOrderEvent]:
    return list(session.scalars(select(OnlineOrderEvent).where(OnlineOrderEvent.shop_id == order.shop_id, OnlineOrderEvent.order_id == order.id).order_by(OnlineOrderEvent.id)))


_REFERENCE = re.compile(r"^ORD-(\d{4}-\d{2})-(\d{4,})$")


def public_reference(order_no: str) -> str:
    """The order number in a form that is safe in a web address: ORD/2026-27/0001 becomes ORD-2026-27-0001."""
    return order_no.replace("/", "-")


def _order_no_of(reference: str) -> str:
    match = _REFERENCE.fullmatch((reference or "").strip())
    if match is None:
        raise NotFoundError("This order was not found.")
    return f"ORD/{match.group(1)}/{match.group(2)}"


def _public_order(session: Session, slug: str, order_no: str, token: str, *, lock: bool = False) -> tuple[StoreSettings, OnlineOrder]:
    store = _public_store(session, slug)
    query = select(OnlineOrder).where(OnlineOrder.shop_id == store.shop_id, OnlineOrder.order_no == _order_no_of(order_no))
    order = session.scalar(query.with_for_update() if lock else query)
    if order is None or not hmac.compare_digest(order.tracking_hash, _hash(token or "")):
        raise NotFoundError("This order was not found.")  # the same answer for a wrong number and a wrong token
    return store, order


@dataclass
class PublicOrderView:
    store: StoreSettings
    order: OnlineOrder
    items: list[OnlineOrderItem]
    events: list[OnlineOrderEvent]


def public_order_view(session: Session, slug: str, order_no: str, token: str) -> PublicOrderView:
    store, order = _public_order(session, slug, order_no, token)
    return PublicOrderView(store, order, _items(session, order), _events(session, order))


def customer_cancel(session: Session, slug: str, order_no: str, token: str) -> PublicOrderView:
    """The customer withdraws an order the shop has not accepted yet."""
    store, order = _public_order(session, slug, order_no, token, lock=True)
    if order.status is not S.PLACED:
        raise ConflictError("The shop has already started on this order. Please call the shop to change it.", code="order_in_progress")
    _move(session, order, S.CANCELLED, actor="CUSTOMER", user_id=None, note="Cancelled by the customer")
    return PublicOrderView(store, order, _items(session, order), _events(session, order))


# --- Staff ---------------------------------------------------------------------------------------------------------


def _move(session: Session, order: OnlineOrder, to: OnlineOrderStatus, *, actor: str, user_id: int | None, note: str | None) -> None:
    if to not in TRANSITIONS[order.status]:
        raise ConflictError(f"An order that is {order.status.value.replace('_', ' ').lower()} cannot become {to.value.replace('_', ' ').lower()}.", code="bad_transition")
    session.add(OnlineOrderEvent(shop_id=order.shop_id, order_id=order.id, from_status=order.status.value, to_status=to.value, actor=actor, user_id=user_id, note=note))
    order.status = to
    session.flush()


@dataclass
class OrderRow:
    order: OnlineOrder
    item_count: int


def list_orders(session: Session, shop_id: int, *, statuses: Sequence[OnlineOrderStatus] | None, q: str | None, date_from: date | None, date_to: date | None, limit: int, offset: int) -> tuple[list[OrderRow], int]:
    query = select(OnlineOrder).where(OnlineOrder.shop_id == shop_id)
    if statuses:
        query = query.where(OnlineOrder.status.in_(list(statuses)))
    if q and q.strip():
        like = f"%{q.strip()[:60].lower()}%"
        query = query.where(or_(func.lower(OnlineOrder.order_no).like(like), func.lower(OnlineOrder.customer_name).like(like), OnlineOrder.customer_phone.like(like)))
    if date_from:
        query = query.where(OnlineOrder.placed_at >= datetime.combine(date_from, datetime.min.time()).astimezone())
    if date_to:
        query = query.where(OnlineOrder.placed_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()).astimezone())
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(session.scalars(query.order_by(OnlineOrder.id.desc()).limit(limit).offset(offset)))
    counts = dict(session.execute(select(OnlineOrderItem.order_id, func.count()).where(OnlineOrderItem.shop_id == shop_id, OnlineOrderItem.order_id.in_([o.id for o in rows] or [0])).group_by(OnlineOrderItem.order_id)).all())
    return [OrderRow(o, counts.get(o.id, 0)) for o in rows], total


@dataclass
class OrderDetail:
    order: OnlineOrder
    items: list[OnlineOrderItem]
    events: list[OnlineOrderEvent]
    invoice_no: str | None = None
    warnings: list[str] = field(default_factory=list)


def _get(session: Session, shop_id: int, order_id: int, *, lock: bool = False) -> OnlineOrder:
    query = select(OnlineOrder).where(OnlineOrder.shop_id == shop_id, OnlineOrder.id == order_id)
    order = session.scalar(query.with_for_update() if lock else query)
    if order is None:
        raise NotFoundError("Order not found.")
    return order


def get_order(session: Session, shop_id: int, order_id: int) -> OrderDetail:
    from app.models import Sale

    order = _get(session, shop_id, order_id)
    invoice = session.scalar(select(Sale.invoice_no).where(Sale.shop_id == shop_id, Sale.id == order.sale_id)) if order.sale_id else None
    detail = OrderDetail(order, _items(session, order), _events(session, order), invoice)
    if order.status in OPEN_STATUSES:  # a heads-up, computed now, never stored
        stock = inventory_service.get_stock_map(session, shop_id, [i.product_id for i in detail.items])
        if not get_shop(session, shop_id).allow_negative_stock:
            for item in detail.items:
                if stock[item.product_id] < item.quantity:
                    detail.warnings.append(f"Only {stock[item.product_id].normalize():f} {item.unit_label} of {item.product_name} in stock now; the order needs {item.quantity.normalize():f}.")
    return detail


def summary(session: Session, shop_id: int) -> dict[str, Any]:
    counts = {s.value: 0 for s in OnlineOrderStatus}
    for status, n in session.execute(select(OnlineOrder.status, func.count()).where(OnlineOrder.shop_id == shop_id).group_by(OnlineOrder.status)):
        counts[status.value] = n
    delivered_total = session.scalar(select(func.sum(OnlineOrder.sale_total)).where(OnlineOrder.shop_id == shop_id, OnlineOrder.status == S.DELIVERED))
    return {"by_status": counts, "open": sum(counts[s.value] for s in OPEN_STATUSES), "delivered_value": delivered_total or ZERO}


def _link_customer(session: Session, ctx: RequestContext, order: OnlineOrder) -> None:
    """Attach the order to the customer with this phone, or create them (as an ONLINE customer). A deactivated customer is not used."""
    customer = session.scalar(select(Customer).where(Customer.shop_id == ctx.shop_id, Customer.phone == order.customer_phone))
    if customer is None:
        result = customer_service.create_customer(session, ctx, {"name": order.customer_name, "phone": order.customer_phone, "address": order.delivery_address})
        customer = result.customer
        customer.source = CustomerSource.ONLINE
        session.flush()
    if customer.is_active:
        order.customer_id = customer.id


def accept(session: Session, ctx: RequestContext, order_id: int, note: str | None = None) -> OrderDetail:
    order = _get(session, ctx.shop_id, order_id, lock=True)
    _move(session, order, S.ACCEPTED, actor="STAFF", user_id=ctx.user_id, note=_clean(note, field_name="note", maximum=300))
    order.decided_by = ctx.user_id
    _link_customer(session, ctx, order)
    _audit(session, ctx, order, "accept")
    _notify(session, order, S.ACCEPTED)
    return get_order(session, ctx.shop_id, order.id)


def reject(session: Session, ctx: RequestContext, order_id: int, reason: str) -> OrderDetail:
    reason = _clean(reason, field_name="reason", minimum=3, maximum=300, required=True)
    order = _get(session, ctx.shop_id, order_id, lock=True)
    _move(session, order, S.REJECTED, actor="STAFF", user_id=ctx.user_id, note=reason)
    order.decided_by, order.decision_reason = ctx.user_id, reason
    _audit(session, ctx, order, "reject")
    _notify(session, order, S.REJECTED)
    return get_order(session, ctx.shop_id, order.id)


def cancel(session: Session, ctx: RequestContext, order_id: int, reason: str) -> OrderDetail:
    reason = _clean(reason, field_name="reason", minimum=3, maximum=300, required=True)
    order = _get(session, ctx.shop_id, order_id, lock=True)
    _move(session, order, S.CANCELLED, actor="STAFF", user_id=ctx.user_id, note=reason)
    order.decision_reason = reason
    _audit(session, ctx, order, "cancel")
    return get_order(session, ctx.shop_id, order.id)


def advance(session: Session, ctx: RequestContext, order_id: int, to: OnlineOrderStatus, note: str | None = None) -> OrderDetail:
    """Move an accepted order along. DELIVERED creates and posts the ordinary sale in this same transaction (all or nothing)."""
    if to not in (S.PREPARING, S.READY, S.OUT_FOR_DELIVERY, S.DELIVERED):
        raise InvalidInputError("Choose preparing, ready, out for delivery or delivered.", field="status")
    order = _get(session, ctx.shop_id, order_id, lock=True)
    if to is S.OUT_FOR_DELIVERY and order.fulfilment_type is not OnlineOrderFulfilment.DELIVERY:
        raise ConflictError("A pickup order is not sent out for delivery.", code="bad_transition")
    if to is S.DELIVERED and order.status is S.READY and order.fulfilment_type is OnlineOrderFulfilment.DELIVERY:
        raise ConflictError("Send a delivery order out first, then mark it delivered.", code="bad_transition")
    if to not in TRANSITIONS[order.status]:
        _move(session, order, to, actor="STAFF", user_id=ctx.user_id, note=None)  # raises the standard message
    if to is S.DELIVERED:
        _fulfil(session, ctx, order)
    _move(session, order, to, actor="STAFF", user_id=ctx.user_id, note=_clean(note, field_name="note", maximum=300))
    _audit(session, ctx, order, to.value.lower())
    _notify(session, order, to)
    return get_order(session, ctx.shop_id, order.id)


def _fulfil(session: Session, ctx: RequestContext, order: OnlineOrder) -> None:
    """Create and post the ordinary Detailed Sale for this order. If the stock is gone the whole delivery is refused and nothing changes."""
    items = _items(session, order)
    header: dict[str, Any] = {"customer_id": order.customer_id, "notes": f"Online order {order.order_no}"}
    lines = [{"product_id": i.product_id, "quantity": i.quantity, "unit_price": i.unit_price} for i in items]
    view = sale_service.create_sale(session, ctx, header, lines)
    method = PaymentMethod.CASH if order.payment_method is OnlineOrderPayment.COD else PaymentMethod.UPI
    posted = sale_service.post_sale(session, ctx, view.sale.id, payment_method=method)  # paid in full, by hand, at the door or counter
    order.sale_id = posted.sale.id
    order.sale_total = posted.sale.total_amount
    session.flush()


def _audit(session: Session, ctx: RequestContext, order: OnlineOrder, action: str) -> None:
    record_audit(session, ctx, entity_type="online_order", entity_id=order.id, action=action, after={"order_no": order.order_no, "status": order.status.value, "sale_id": order.sale_id})


def _notify(session: Session, order: OnlineOrder, status: OnlineOrderStatus) -> None:
    event = EVENT_FOR.get(status)
    if event:
        notification_service.emit_safely(
            session, order.shop_id, event, title=f"Online order {status.value.replace('_', ' ').lower()}",
            message=f"{order.order_no} is {status.value.replace('_', ' ').lower()}.", dedupe_key=f"online-order-{order.id}-{status.value.lower()}",
            entity_type="online_order", entity_id=order.id,
        )  # fmt: skip
