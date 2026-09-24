"""Stock counting (cycle counting): a physical count reconciled against the ledger, under a workflow that only
writes to `inventory_transactions` once it is posted, and only through `inventory_service`.

`stock_count_items.expected_quantity` is a SNAPSHOT taken when the count starts (the stock the ledger showed
then), not a live read: counting can take hours, and the ledger keeps moving (other sales, other adjustments).
The variance a person reviews is against that snapshot, so what they see explains itself. Nothing here
duplicates current stock as a balance: `expected_quantity` is a point-in-time copy, and the only lasting
effect of a count is the adjustment rows it creates."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity, UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import StockCountScope, StockCountStatus


class StockCount(TimestampMixin, Base):
    __tablename__ = "stock_counts"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("category_id", "categories"),
        tenant_fk("created_by", "users"),
        tenant_fk("reviewed_by", "users"),
        tenant_fk("approved_by", "users"),
        tenant_fk("posted_by", "users"),
        Index("ix_stock_counts_shop_status", "shop_id", "status"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    title: Mapped[str] = mapped_column(String(200))
    scope: Mapped[StockCountScope] = mapped_column(enum_type(StockCountScope, "stock_count_scope"))
    category_id: Mapped[int | None] = mapped_column(IdType)  # set when scope is CATEGORY
    status: Mapped[StockCountStatus] = mapped_column(
        enum_type(StockCountStatus, "stock_count_status"),
        default=StockCountStatus.DRAFT,
        server_default=StockCountStatus.DRAFT.value,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # Set only when a large variance needs a second, separate approval (see `approval_requests`); most
    # counts skip it.
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[int] = mapped_column(IdType)
    reviewed_by: Mapped[int | None] = mapped_column(IdType)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approved_by: Mapped[int | None] = mapped_column(IdType)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    posted_by: Mapped[int | None] = mapped_column(IdType)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by: Mapped[int | None] = mapped_column(IdType)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(Text)


class StockCountItem(TimestampMixin, Base):
    """One product's line in a count. `variance` = `counted_quantity` - `expected_quantity`, computed at
    review time (both are then fixed), never a live comparison."""

    __tablename__ = "stock_count_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("stock_count_id", "product_id"),
        tenant_fk("stock_count_id", "stock_counts"),
        tenant_fk("product_id", "products"),
        tenant_fk("counted_by", "users"),
        Index("ix_stock_count_items_shop_count", "shop_id", "stock_count_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    stock_count_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    expected_quantity: Mapped[Decimal] = mapped_column(Quantity)
    counted_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    variance: Mapped[Decimal | None] = mapped_column(Quantity)
    # The average cost at the time of the count, if known: for valuing the variance. NULL means unknown,
    # never 0.
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(Money)
    note: Mapped[str | None] = mapped_column(Text)
    counted_by: Mapped[int | None] = mapped_column(IdType)
    counted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
