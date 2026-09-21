"""Purchase documents (stock coming in) and purchase returns.

Database foundation only: the workflows that create these rows arrive in Phases 5 and 9.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity, UTCDateTime
from app.models.base import (
    Base,
    DocumentLifecycleMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
    void_requires_reason,
)
from app.models.enums import PaymentMethod, PurchaseStatus, SupplierCreditMode


class Purchase(TimestampMixin, Base):
    """Goods received from a supplier.

    Lifecycle (`status`): DRAFT -> POSTED -> VOID.
      * DRAFT: work in progress. It has no number and never affects stock or cost.
      * POSTED: assigned a shop-scoped number; one PURCHASE row per item is in the stock ledger.
      * VOID: the purchase was cancelled. Its stock effect is undone by REVERSAL ledger rows; the row and
        its number stay forever (a draft that is discarded is VOID too, with no number and no ledger rows).
    Posted purchases are never edited or deleted: correct one by voiding it and entering a corrected copy.
    """

    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "purchase_no"),  # NULL for drafts; NULLs never collide
        UniqueConstraint("replaces_id"),
        # The supplier's own invoice number is optional (small dealers often give none). While a purchase
        # is a draft or posted, it may not repeat for the same supplier: that catches double entry. A VOID
        # purchase releases its number, so a corrected copy can be entered with the same invoice number.
        Index(
            "uq_purchases_supplier_invoice_active",
            "shop_id",
            "supplier_id",
            "supplier_invoice_no",
            unique=True,
            sqlite_where=text("status <> 'VOID'"),
            postgresql_where=text("status <> 'VOID'"),
        ),
        tenant_fk("supplier_id", "suppliers"),
        tenant_fk("replaces_id", "purchases"),
        tenant_fk("created_by", "users"),
        tenant_fk("posted_by", "users"),
        Index("ix_purchases_shop_date", "shop_id", "purchase_date"),
        Index("ix_purchases_shop_supplier_date", "shop_id", "supplier_id", "purchase_date"),
        non_negative("total_amount"),
        non_negative("amount_paid"),
        CheckConstraint("amount_paid <= total_amount", name="paid_not_above_total"),
        CheckConstraint("amount_paid = 0 OR payment_method IS NOT NULL", name="payment_needs_method"),
        CheckConstraint("status <> 'VOID' OR void_reason IS NOT NULL", name="void_needs_reason"),
        # A purchase number and its posting time exist together, and every posted purchase has them.
        CheckConstraint(
            "(purchase_no IS NULL AND posted_at IS NULL)"
            " OR (purchase_no IS NOT NULL AND posted_at IS NOT NULL)",
            name="number_and_posted_at_together",
        ),
        CheckConstraint("status <> 'POSTED' OR purchase_no IS NOT NULL", name="posted_needs_number"),
        CheckConstraint("status <> 'DRAFT' OR purchase_no IS NULL", name="draft_has_no_number"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    purchase_no: Mapped[str | None] = mapped_column(String(30))  # e.g. PUR/2026-27/0001, set when posted
    supplier_id: Mapped[int] = mapped_column(IdType)
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(50))
    purchase_date: Mapped[date] = mapped_column(Date)
    # The sum of the item line totals (net of discounts). Kept in step by `purchase_service`.
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    # Reserved for the supplier-payments phase. Nothing writes these yet.
    amount_paid: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PurchaseStatus] = mapped_column(
        enum_type(PurchaseStatus, "status"), default=PurchaseStatus.DRAFT
    )
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    posted_by: Mapped[int | None] = mapped_column(IdType)
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)


class PurchaseItem(TimestampMixin, Base):
    """One product line of a purchase.

    `unit_cost` is the price per unit before discount; `discount` is a money amount off the whole line;
    `line_total` = round(quantity x unit_cost) - discount is what the line really costs. **Costing uses the
    net `line_total`**, never the pre-discount price. `unit_id` records the unit the quantity is counted in.
    The three `*_before/after` columns are a snapshot taken at posting (NULL for drafts): the stock and the
    average cost just before this line arrived, and the average cost just after. NULL cost means unknown.
    """

    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("purchase_id", "purchases"),
        tenant_fk("product_id", "products"),
        Index("ix_purchase_items_shop_purchase", "shop_id", "purchase_id"),
        Index("ix_purchase_items_shop_product", "shop_id", "product_id"),
        positive("quantity"),
        non_negative("unit_cost"),
        non_negative("discount"),
        non_negative("line_total"),
        non_negative("avg_cost_before"),
        non_negative("avg_cost_after"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    purchase_id: Mapped[int] = mapped_column(IdType)
    product_id: Mapped[int] = mapped_column(IdType)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_cost: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Money)
    stock_before: Mapped[Decimal | None] = mapped_column(Quantity)
    avg_cost_before: Mapped[Decimal | None] = mapped_column(Money)
    avg_cost_after: Mapped[Decimal | None] = mapped_column(Money)


class PurchaseReturn(DocumentLifecycleMixin, TimestampMixin, Base):
    """Goods sent back to a supplier. Always refers to the original purchase."""

    __tablename__ = "purchase_returns"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "return_no"),
        not_blank("return_no"),
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
    return_no: Mapped[str] = mapped_column(String(30))  # e.g. PRT/2026-27/0001
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
