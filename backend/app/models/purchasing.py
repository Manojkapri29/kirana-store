"""Purchase documents (stock coming in) and purchase returns.

Database foundation only: the workflows that create these rows arrive in Phases 5 and 9.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity
from app.models.base import (
    Base,
    DocumentLifecycleMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    positive,
    shop_id_column,
    tenant_fk,
    void_requires_reason,
)
from app.models.enums import PaymentMethod, SupplierCreditMode


class Purchase(DocumentLifecycleMixin, TimestampMixin, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        # The supplier's own invoice number. Optional (small dealers often give none); when present it
        # must not repeat for the same supplier, which catches double entry.
        UniqueConstraint("shop_id", "supplier_id", "supplier_invoice_no"),
        UniqueConstraint("replaces_id"),
        tenant_fk("supplier_id", "suppliers"),
        tenant_fk("replaces_id", "purchases"),
        tenant_fk("created_by", "users"),
        Index("ix_purchases_shop_date", "shop_id", "purchase_date"),
        non_negative("total_amount"),
        non_negative("amount_paid"),
        CheckConstraint("amount_paid <= total_amount", name="paid_not_above_total"),
        CheckConstraint("amount_paid = 0 OR payment_method IS NOT NULL", name="payment_needs_method"),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    supplier_id: Mapped[int] = mapped_column(IdType)
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(50))
    purchase_date: Mapped[date] = mapped_column(Date)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    amount_paid: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)


class PurchaseItem(TimestampMixin, Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("purchase_id", "purchases"),
        tenant_fk("product_id", "products"),
        Index("ix_purchase_items_shop_purchase", "shop_id", "purchase_id"),
        Index("ix_purchase_items_shop_product", "shop_id", "product_id"),
        positive("quantity"),
        non_negative("unit_cost"),
        non_negative("line_total"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    purchase_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_cost: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)


class PurchaseReturn(DocumentLifecycleMixin, TimestampMixin, Base):
    """Goods sent back to a supplier. Always refers to the original purchase."""

    __tablename__ = "purchase_returns"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("replaces_id"),
        tenant_fk("purchase_id", "purchases"),
        tenant_fk("replaces_id", "purchase_returns"),
        tenant_fk("created_by", "users"),
        Index("ix_purchase_returns_shop_date", "shop_id", "return_date"),
        non_negative("total_amount"),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    purchase_id: Mapped[int] = mapped_column(IdType)
    return_date: Mapped[date] = mapped_column(Date)
    credit_mode: Mapped[SupplierCreditMode] = mapped_column(enum_type(SupplierCreditMode, "credit_mode"))
    total_amount: Mapped[Decimal] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)


class PurchaseReturnItem(TimestampMixin, Base):
    __tablename__ = "purchase_return_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("purchase_return_id", "purchase_returns"),
        tenant_fk("purchase_item_id", "purchase_items"),
        tenant_fk("product_id", "products"),
        Index("ix_purchase_return_items_shop_return", "shop_id", "purchase_return_id"),
        Index("ix_purchase_return_items_shop_item", "shop_id", "purchase_item_id"),
        positive("quantity"),
        non_negative("unit_cost"),
        non_negative("line_total"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    purchase_return_id: Mapped[int] = mapped_column(IdType)
    # The original line being returned; total returned per line is capped by services (R1).
    purchase_item_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_cost: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)
