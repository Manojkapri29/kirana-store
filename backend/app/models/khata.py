"""The customer ledger (khata).

`customer_ledger` is INSERT-ONLY, like the stock ledger. A customer's outstanding balance is the sum of
`amount_delta` (positive = the customer owes the shop). `khata_service` (Phase 6) will be the ONLY
module that writes to this table.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    all_or_none,
    enum_type,
    id_column,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import CustomerLedgerEntryType, KhataReferenceType, PaymentMethod


class CustomerLedgerEntry(CreatedAtMixin, Base):
    __tablename__ = "customer_ledger"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint(
            "shop_id",
            "reference_type",
            "reference_id",
            "entry_type",
            name="uq_customer_ledger_reference",
        ),
        UniqueConstraint("reverses_entry_id", name="uq_customer_ledger_reverses"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("reverses_entry_id", "customer_ledger"),
        tenant_fk("created_by", "users"),
        Index("ix_customer_ledger_shop_customer_date", "shop_id", "customer_id", "entry_date"),
        # Sign must match the type: credit sales add to what is owed; payments and return credits reduce it.
        CheckConstraint(
            "(entry_type = 'CREDIT_SALE' AND amount_delta > 0)"
            " OR (entry_type IN ('PAYMENT', 'RETURN_CREDIT') AND amount_delta < 0)"
            " OR (entry_type IN ('OPENING_BALANCE', 'ADJUSTMENT', 'REVERSAL') AND amount_delta <> 0)",
            name="sign_matches_type",
        ),
        CheckConstraint(
            "(entry_type = 'REVERSAL' AND reverses_entry_id IS NOT NULL)"
            " OR (entry_type <> 'REVERSAL' AND reverses_entry_id IS NULL)",
            name="reversal_links_original",
        ),
        CheckConstraint("payment_method IS NULL OR entry_type = 'PAYMENT'", name="method_only_for_payment"),
        all_or_none("reference_pair", "reference_type", "reference_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    customer_id: Mapped[int] = mapped_column(IdType)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_type: Mapped[CustomerLedgerEntryType] = mapped_column(
        enum_type(CustomerLedgerEntryType, "entry_type")
    )
    # Signed: positive = the customer owes more, negative = the customer owes less.
    amount_delta: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    payment_reference: Mapped[str | None] = mapped_column(String(100))
    reference_type: Mapped[KhataReferenceType | None] = mapped_column(
        enum_type(KhataReferenceType, "reference_type")
    )
    reference_id: Mapped[int | None] = mapped_column(IdType)
    reverses_entry_id: Mapped[int | None] = mapped_column(IdType)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(IdType)
