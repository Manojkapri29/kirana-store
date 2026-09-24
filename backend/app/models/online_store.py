"""The online store: a shop's public storefront settings, which products it lists, and the orders customers place.

An order is a REQUEST. Placing one moves no stock and no money. The shop accepts it and moves it along; only when it is DELIVERED is an
ordinary Detailed Sale created and posted through `sale_service` (the same path as a counter sale), so stock, cost, khata, returns,
reports and finance behave exactly as they do for a shop sale. There is no second inventory and no second sales ledger.

Order lines snapshot the name, unit and price the customer was shown; the price always comes from the product on the server, never from
the browser. Order history rows are insert-only.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, Quantity, UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import OnlineOrderFulfilment, OnlineOrderPayment, OnlineOrderStatus


class StoreSettings(TimestampMixin, Base):
    __tablename__ = "store_settings"
    __table_args__ = (
        UniqueConstraint("shop_id"),
        UniqueConstraint("slug"),
        UniqueConstraint("shop_id", "id"),
        not_blank("slug"),
        not_blank("display_name"),
        CheckConstraint("min_order_amount >= 0", name="min_order_non_negative"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    slug: Mapped[str] = mapped_column(String(40))  # the public address: /store/<slug>
    display_name: Mapped[str] = mapped_column(String(120))
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false())
    accepts_cod: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    accepts_upi: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    min_order_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    announcement: Mapped[str | None] = mapped_column(String(300))


class StoreListing(TimestampMixin, Base):
    """Which products the shop shows online. A product with no row is not shown."""

    __tablename__ = "store_listings"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "product_id"),
        tenant_fk("product_id", "products"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    product_id: Mapped[int] = mapped_column(IdType)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class OnlineOrder(TimestampMixin, Base):
    __tablename__ = "online_orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "order_no"),
        UniqueConstraint("shop_id", "idempotency_key"),
        UniqueConstraint("shop_id", "sale_id"),  # an order becomes at most one sale
        UniqueConstraint("tracking_hash"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("sale_id", "sales"),
        tenant_fk("decided_by", "users"),
        not_blank("customer_name"),
        not_blank("customer_phone"),
        CheckConstraint("total_amount > 0", name="total_positive"),
        CheckConstraint(
            "fulfilment_type <> 'DELIVERY' OR (delivery_address IS NOT NULL AND length(trim(delivery_address)) > 0)",
            name="delivery_needs_address",
        ),
        CheckConstraint("status <> 'DELIVERED' OR sale_id IS NOT NULL", name="delivered_has_sale"),
        Index("ix_online_orders_shop_status", "shop_id", "status"),
        Index("ix_online_orders_shop_phone", "shop_id", "customer_phone"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    order_no: Mapped[str] = mapped_column(String(30))
    tracking_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 of the tracking token; the token itself is never stored
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))  # the same key with a different order is refused
    status: Mapped[OnlineOrderStatus] = mapped_column(
        enum_type(OnlineOrderStatus, "online_order_status"), default=OnlineOrderStatus.PLACED
    )
    fulfilment_type: Mapped[OnlineOrderFulfilment] = mapped_column(
        enum_type(OnlineOrderFulfilment, "online_order_fulfilment")
    )
    payment_method: Mapped[OnlineOrderPayment] = mapped_column(enum_type(OnlineOrderPayment, "online_order_payment"))
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(20))
    delivery_address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(300))
    total_amount: Mapped[Decimal] = mapped_column(Money)  # what the customer was quoted
    customer_id: Mapped[int | None] = mapped_column(IdType)
    sale_id: Mapped[int | None] = mapped_column(IdType)
    sale_total: Mapped[Decimal | None] = mapped_column(Money)  # what the sale actually came to (an offer may lower it)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_by: Mapped[int | None] = mapped_column(IdType)
    decision_reason: Mapped[str | None] = mapped_column(String(300))


class OnlineOrderItem(CreatedAtMixin, Base):
    __tablename__ = "online_order_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("order_id", "online_orders"),
        tenant_fk("product_id", "products"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        Index("ix_online_order_items_order", "shop_id", "order_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    order_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    product_name: Mapped[str] = mapped_column(String(200))
    unit_label: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)


class OnlineOrderEvent(CreatedAtMixin, Base):
    """Every change of an order's status and who caused it. Insert-only."""

    __tablename__ = "online_order_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("order_id", "online_orders"),
        Index("ix_online_order_events_order", "shop_id", "order_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    order_id: Mapped[int] = mapped_column(IdType)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(10))  # CUSTOMER | STAFF
    user_id: Mapped[int | None] = mapped_column(IdType)
    note: Mapped[str | None] = mapped_column(String(300))
