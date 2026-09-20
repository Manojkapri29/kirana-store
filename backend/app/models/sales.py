"""Sales documents.

Two structurally different kinds of sale (BUSINESS_RULES S1, S2):

* `sales` + `sale_items`  Detailed Sale: product-wise. Each line will post a SALE row to the stock
                          ledger and stores a cost snapshot, so COGS and profit can be computed.
* `quick_sales`           Quick/Daily Sale: money only. This table has NO product, quantity or cost
                          columns, so it cannot express stock movement or product profit at all. That
                          is deliberate; a test guards it.

Database foundation only: the workflows that create these rows arrive in Phases 7 to 9.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity
from app.models.base import (
    Base,
    DocumentLifecycleMixin,
    IdType,
    TimestampMixin,
    all_or_none,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    payment_rules,
    positive,
    shop_id_column,
    tenant_fk,
    void_requires_reason,
)
from app.models.enums import PaymentMethod, PaymentType, RefundMode


class Sale(DocumentLifecycleMixin, TimestampMixin, Base):
    """A Detailed Sale (a bill)."""

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "invoice_no"),
        UniqueConstraint("replaces_id"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("replaces_id", "sales"),
        tenant_fk("created_by", "users"),
        Index("ix_sales_shop_date", "shop_id", "sale_date"),
        Index("ix_sales_shop_customer", "shop_id", "customer_id"),
        not_blank("invoice_no"),
        *payment_rules(),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    invoice_no: Mapped[str] = mapped_column(String(30))
    sale_date: Mapped[date] = mapped_column(Date)
    customer_id: Mapped[int | None] = mapped_column(IdType)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    payment_type: Mapped[PaymentType] = mapped_column(enum_type(PaymentType, "payment_type"))
    amount_paid: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)


class SaleItem(TimestampMixin, Base):
    """One product line of a Detailed Sale. Price, MRP and cost are snapshots taken at sale time."""

    __tablename__ = "sale_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("sale_id", "sales"),
        tenant_fk("product_id", "products"),
        Index("ix_sale_items_shop_sale", "shop_id", "sale_id"),
        Index("ix_sale_items_shop_product", "shop_id", "product_id"),
        positive("quantity"),
        non_negative("unit_price"),
        non_negative("mrp"),
        non_negative("discount"),
        non_negative("line_total"),  # gross - discount; also stops a discount above the gross
        non_negative("unit_cost"),
        non_negative("cogs_amount"),
        # Cost is known (both set) or unknown (both NULL). Unknown is never stored as 0 (C3).
        all_or_none("cost_known_or_unknown", "unit_cost", "cogs_amount"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sale_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Money)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    cogs_amount: Mapped[Decimal | None] = mapped_column(Money)


class SalesReturn(DocumentLifecycleMixin, TimestampMixin, Base):
    """Goods a customer brings back. Always refers to the original Detailed Sale."""

    __tablename__ = "sales_returns"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("replaces_id"),
        tenant_fk("sale_id", "sales"),
        tenant_fk("replaces_id", "sales_returns"),
        tenant_fk("created_by", "users"),
        Index("ix_sales_returns_shop_date", "shop_id", "return_date"),
        non_negative("total_refund"),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sale_id: Mapped[int] = mapped_column(IdType)
    return_date: Mapped[date] = mapped_column(Date)
    refund_mode: Mapped[RefundMode] = mapped_column(enum_type(RefundMode, "refund_mode"))
    total_refund: Mapped[Decimal] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)


class SalesReturnItem(TimestampMixin, Base):
    __tablename__ = "sales_return_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("sales_return_id", "sales_returns"),
        tenant_fk("sale_item_id", "sale_items"),
        tenant_fk("product_id", "products"),
        Index("ix_sales_return_items_shop_return", "shop_id", "sales_return_id"),
        Index("ix_sales_return_items_shop_item", "shop_id", "sale_item_id"),
        positive("quantity"),
        non_negative("refund_amount"),
        non_negative("unit_cost"),
        non_negative("cogs_amount"),
        all_or_none("cost_known_or_unknown", "unit_cost", "cogs_amount"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sales_return_id: Mapped[int] = mapped_column(IdType)
    # The original line being returned; total returned per line is capped by services (R1).
    sale_item_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    refund_amount: Mapped[Decimal] = mapped_column(Money)
    # Copied from the ORIGINAL sale line, so profit reverses at the cost it was earned at (R2).
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    cogs_amount: Mapped[Decimal | None] = mapped_column(Money)


class QuickSale(DocumentLifecycleMixin, TimestampMixin, Base):
    """A Quick/Daily Sale: money only.

    There are intentionally no product, quantity, cost or COGS columns. A quick sale never touches
    stock, and its profit is "Not Available" (BUSINESS_RULES S2, F2).
    """

    __tablename__ = "quick_sales"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("replaces_id"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("replaces_id", "quick_sales"),
        tenant_fk("created_by", "users"),
        Index("ix_quick_sales_shop_date", "shop_id", "sale_date"),
        *payment_rules(total_must_be_positive=True),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sale_date: Mapped[date] = mapped_column(Date)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    customer_id: Mapped[int | None] = mapped_column(IdType)  # for quick sales on credit
    payment_type: Mapped[PaymentType] = mapped_column(enum_type(PaymentType, "payment_type"))
    amount_paid: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)
