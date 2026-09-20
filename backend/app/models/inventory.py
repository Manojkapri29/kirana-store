"""The stock ledger.

`inventory_transactions` is INSERT-ONLY: rows are never updated or deleted (a database trigger added
by the migration enforces this). Current stock is the sum of `qty_delta` per product. Corrections are
new rows: returns, adjustments, or REVERSAL rows that undo an earlier row.

`inventory_service` (Phase 3) will be the ONLY module that writes to this table. See
`docs/ARCHITECTURE.md` and the architecture test that guards this rule.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    all_or_none,
    enum_type,
    id_column,
    non_negative,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import AdjustmentReason, InventoryTxnType, StockReferenceType


class InventoryTransaction(CreatedAtMixin, Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        # A document line can post each transaction type at most once.
        UniqueConstraint(
            "shop_id",
            "reference_type",
            "reference_id",
            "txn_type",
            name="uq_inventory_transactions_reference",
        ),
        # A ledger row can be reversed at most once (NULLs are ignored by UNIQUE).
        UniqueConstraint("reverses_txn_id", name="uq_inventory_transactions_reverses"),
        tenant_fk("product_id", "products"),
        tenant_fk("reverses_txn_id", "inventory_transactions"),
        tenant_fk("created_by", "users"),
        Index("ix_inventory_transactions_shop_product_date", "shop_id", "product_id", "txn_date"),
        Index("ix_inventory_transactions_shop_date", "shop_id", "txn_date"),
        # Sign must match the type (BUSINESS_RULES L3).
        CheckConstraint(
            "(txn_type IN ('OPENING', 'PURCHASE', 'SALE_RETURN') AND qty_delta > 0)"
            " OR (txn_type IN ('SALE', 'PURCHASE_RETURN') AND qty_delta < 0)"
            " OR (txn_type IN ('ADJUSTMENT', 'REVERSAL') AND qty_delta <> 0)",
            name="sign_matches_type",
        ),
        non_negative("unit_cost"),
        # A reversal points at the row it undoes; nothing else does.
        CheckConstraint(
            "(txn_type = 'REVERSAL' AND reverses_txn_id IS NOT NULL)"
            " OR (txn_type <> 'REVERSAL' AND reverses_txn_id IS NULL)",
            name="reversal_links_original",
        ),
        # Adjustments need an explicit reason; only adjustments carry one; OTHER also needs a note (A1).
        CheckConstraint(
            "(txn_type = 'ADJUSTMENT' AND reason_code IS NOT NULL)"
            " OR (txn_type <> 'ADJUSTMENT' AND reason_code IS NULL)",
            name="reason_only_for_adjustment",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code <> 'OTHER' OR length(trim(coalesce(note, ''))) > 0",
            name="other_reason_needs_note",
        ),
        all_or_none("reference_pair", "reference_type", "reference_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    product_id: Mapped[int] = mapped_column(IdType)
    txn_type: Mapped[InventoryTxnType] = mapped_column(enum_type(InventoryTxnType, "txn_type"))
    # Signed: positive adds stock, negative removes it.
    qty_delta: Mapped[Decimal] = mapped_column(Quantity)
    # Cost per unit of this movement. NULL when unknown, never 0 (BUSINESS_RULES C3).
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    # Business date (shop-local). `created_at` is when the row was actually recorded (UTC).
    txn_date: Mapped[date] = mapped_column(Date)
    # Polymorphic pointer to the source document line, so it has no foreign key; services verify it.
    reference_type: Mapped[StockReferenceType | None] = mapped_column(
        enum_type(StockReferenceType, "reference_type")
    )
    reference_id: Mapped[int | None] = mapped_column(IdType)
    reverses_txn_id: Mapped[int | None] = mapped_column(IdType)
    reason_code: Mapped[AdjustmentReason | None] = mapped_column(enum_type(AdjustmentReason, "reason_code"))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(IdType)
