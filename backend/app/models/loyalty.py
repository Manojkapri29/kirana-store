"""Loyalty (Phase 14): one configurable program per shop, and an INSERT-ONLY ledger.

A customer's point balance is never stored: it is the sum of their `loyalty_ledger` rows, exactly like a
customer's khata balance is the sum of `customer_ledger` rows and a product's stock is the sum of
`inventory_transactions`. Nothing here overwrites a balance; a correction is a new ADJUST or REVERSAL row.

Every earning rule (points per amount, minimum transaction, redemption value, minimum redemption, maximum
redemption per transaction, expiry) is configured per shop on `LoyaltyProgram`, never hardcoded — a NULL rule
means the shop has not set a limit, and `loyalty_service` says so rather than assuming a number.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    positive,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import LoyaltyEntryType


class LoyaltyProgram(TimestampMixin, Base):
    """One per shop. `points_per_amount` = how many points one currency unit of a qualifying sale earns
    (e.g. 0.01 = 1 point per ₹100); `redemption_value` = how much money one point is worth when redeemed.
    Both are `Decimal`, not hardcoded — a shop can run its own scheme."""

    __tablename__ = "loyalty_programs"
    __table_args__ = (
        UniqueConstraint("shop_id"),
        UniqueConstraint("shop_id", "id"),
        positive("points_per_amount"),
        non_negative("min_transaction_amount"),
        positive("redemption_value"),
        positive("min_redemption_points"),
        positive("max_redeem_points_per_txn"),
        positive("points_expiry_days"),
        tenant_fk("created_by", "users"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    points_per_amount: Mapped[Decimal] = mapped_column(Money)
    min_transaction_amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    redemption_value: Mapped[Decimal] = mapped_column(Money)
    min_redemption_points: Mapped[int | None] = mapped_column(Integer)
    max_redeem_points_per_txn: Mapped[int | None] = mapped_column(Integer)
    points_expiry_days: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(IdType)


class LoyaltyLedger(CreatedAtMixin, Base):
    """INSERT-ONLY, exactly like `customer_ledger`. `reference_type`/`reference_id` point at what caused the
    entry (a sale, a quick sale, a manual adjustment, a redemption, a referral reward); the pair is used to
    prevent awarding points twice for the same transaction."""

    __tablename__ = "loyalty_ledger"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint(
            "shop_id",
            "customer_id",
            "entry_type",
            "reference_type",
            "reference_id",
            name="uq_loyalty_ledger_no_double_entry",
        ),  # fmt: skip
        tenant_fk("customer_id", "customers"),
        tenant_fk("created_by", "users"),
        Index("ix_loyalty_ledger_shop_customer", "shop_id", "customer_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    customer_id: Mapped[int] = mapped_column(IdType)
    entry_type: Mapped[LoyaltyEntryType] = mapped_column(enum_type(LoyaltyEntryType, "loyalty_entry_type"))
    points_delta: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str] = mapped_column(String(20))  # SALE|QUICK_SALE|MANUAL|REDEMPTION|REFERRAL
    reference_id: Mapped[int | None] = mapped_column(IdType)
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(IdType)
