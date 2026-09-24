from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import OnlineOrderFulfilment, OnlineOrderPayment, OnlineOrderStatus
from app.schemas.common import MoneyIn, Page, QuantityIn
from app.services import online_store_service as svc

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]


# --- staff: settings and listing ---------------------------------------------------------------------------------


class StoreSettingsIn(BaseModel):
    """Only the fields sent change. `slug` is required the first time."""

    model_config = ConfigDict(extra="forbid")

    slug: Text | None = None
    display_name: Text | None = None
    is_open: bool | None = None
    accepts_cod: bool | None = None
    accepts_upi: bool | None = None
    delivery_enabled: bool | None = None
    pickup_enabled: bool | None = None
    min_order_amount: MoneyIn | None = None
    contact_phone: Text | None = None
    announcement: Text | None = None


class StoreSettingsOut(BaseModel):
    slug: str
    display_name: str
    is_open: bool
    accepts_cod: bool
    accepts_upi: bool
    delivery_enabled: bool
    pickup_enabled: bool
    min_order_amount: Decimal
    contact_phone: str | None
    announcement: str | None
    public_path: str

    @classmethod
    def from_row(cls, row) -> "StoreSettingsOut":  # noqa: ANN001
        return cls(
            slug=row.slug, display_name=row.display_name, is_open=row.is_open, accepts_cod=row.accepts_cod,
            accepts_upi=row.accepts_upi, delivery_enabled=row.delivery_enabled, pickup_enabled=row.pickup_enabled,
            min_order_amount=row.min_order_amount, contact_phone=row.contact_phone, announcement=row.announcement,
            public_path=f"/store/{row.slug}",
        )  # fmt: skip


class StoreSettingsResult(BaseModel):
    """`store` is null until the shop has set one up."""

    store: StoreSettingsOut | None


class ListingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: bool


class ListingOut(BaseModel):
    product_id: int
    sku: str
    name: str
    category: str
    unit: str
    price: Decimal
    is_active: bool
    is_visible: bool

    @classmethod
    def from_row(cls, r: svc.ListingRow) -> "ListingOut":
        return cls(product_id=r.product_id, sku=r.sku, name=r.name, category=r.category, unit=r.unit, price=r.price, is_active=r.is_active, is_visible=r.is_visible)


class ListingPage(Page):
    items: list[ListingOut]


# --- staff: orders ------------------------------------------------------------------------------------------------


class OrderItemOut(BaseModel):
    product_id: int
    product_name: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class OrderEventOut(BaseModel):
    from_status: str | None
    to_status: str
    actor: str
    note: str | None
    at: datetime


class OrderRowOut(BaseModel):
    id: int
    order_no: str
    status: OnlineOrderStatus
    fulfilment: OnlineOrderFulfilment
    payment: OnlineOrderPayment
    customer_name: str
    customer_phone: str
    total_amount: Decimal
    item_count: int
    placed_at: datetime
    sale_id: int | None


class OrderPage(Page):
    items: list[OrderRowOut]


class OrderDetailOut(BaseModel):
    id: int
    order_no: str
    status: OnlineOrderStatus
    fulfilment: OnlineOrderFulfilment
    payment: OnlineOrderPayment
    customer_name: str
    customer_phone: str
    delivery_address: str | None
    notes: str | None
    total_amount: Decimal
    customer_id: int | None
    sale_id: int | None
    invoice_no: str | None
    sale_total: Decimal | None
    placed_at: datetime
    decision_reason: str | None
    items: list[OrderItemOut]
    events: list[OrderEventOut]
    warnings: list[str]
    allowed_next: list[OnlineOrderStatus]

    @classmethod
    def from_detail(cls, d: svc.OrderDetail) -> "OrderDetailOut":
        o = d.order
        return cls(
            id=o.id, order_no=o.order_no, status=o.status, fulfilment=o.fulfilment_type, payment=o.payment_method,
            customer_name=o.customer_name, customer_phone=o.customer_phone, delivery_address=o.delivery_address, notes=o.notes,
            total_amount=o.total_amount, customer_id=o.customer_id, sale_id=o.sale_id, invoice_no=d.invoice_no, sale_total=o.sale_total,
            placed_at=o.placed_at, decision_reason=o.decision_reason, warnings=d.warnings,
            items=[OrderItemOut(product_id=i.product_id, product_name=i.product_name, unit=i.unit_label, quantity=i.quantity, unit_price=i.unit_price, line_total=i.line_total) for i in d.items],
            events=[OrderEventOut(from_status=e.from_status, to_status=e.to_status, actor=e.actor, note=e.note, at=e.created_at) for e in d.events],
            allowed_next=sorted(svc.TRANSITIONS[o.status], key=lambda s: s.value),
        )  # fmt: skip


class AdvanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OnlineOrderStatus
    note: Text | None = None


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Text = Field(min_length=3)


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Text | None = None


class OrdersSummaryOut(BaseModel):
    by_status: dict[str, int]
    open: int
    delivered_value: Decimal


# --- public storefront --------------------------------------------------------------------------------------------


class PublicStoreOut(BaseModel):
    slug: str
    name: str
    is_open: bool
    accepts_cod: bool
    accepts_upi: bool
    delivery_enabled: bool
    pickup_enabled: bool
    min_order_amount: Decimal
    contact_phone: str | None
    announcement: str | None


class PublicProductOut(BaseModel):
    id: int
    name: str
    category: str
    unit: str
    allows_decimal: bool
    price: Decimal
    in_stock: bool


class PublicProductPage(Page):
    items: list[PublicProductOut]


class PublicLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    quantity: QuantityIn


class PublicOrderIn(BaseModel):
    """What a customer sends. There is no price, total or status here on purpose: the server works them out."""

    model_config = ConfigDict(extra="forbid")

    customer_name: Text
    customer_phone: Text
    fulfilment: OnlineOrderFulfilment
    payment: OnlineOrderPayment
    delivery_address: Text | None = None
    notes: Text | None = None
    items: list[PublicLineIn] = Field(min_length=1, max_length=svc.MAX_LINES)


class PublicOrderItemOut(BaseModel):
    name: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class PublicTimelineOut(BaseModel):
    status: str
    at: datetime


class PublicOrderOut(BaseModel):
    order_no: str
    reference: str  # the same number, safe to put in a web address
    status: OnlineOrderStatus
    fulfilment: OnlineOrderFulfilment
    payment: OnlineOrderPayment
    total_amount: Decimal
    placed_at: datetime
    items: list[PublicOrderItemOut]
    timeline: list[PublicTimelineOut]
    store_name: str
    store_phone: str | None
    can_cancel: bool
    tracking_token: str | None = None  # only in the answer that creates the order

    @classmethod
    def from_view(cls, v: svc.PublicOrderView, *, token: str | None = None) -> "PublicOrderOut":
        o = v.order
        return cls(
            order_no=o.order_no, reference=svc.public_reference(o.order_no), status=o.status, fulfilment=o.fulfilment_type, payment=o.payment_method, total_amount=o.total_amount,
            placed_at=o.placed_at, store_name=v.store.display_name, store_phone=v.store.contact_phone,
            can_cancel=o.status is OnlineOrderStatus.PLACED, tracking_token=token,
            items=[PublicOrderItemOut(name=i.product_name, unit=i.unit_label, quantity=i.quantity, unit_price=i.unit_price, line_total=i.line_total) for i in v.items],
            timeline=[PublicTimelineOut(status=e.to_status, at=e.created_at) for e in v.events],
        )  # fmt: skip
