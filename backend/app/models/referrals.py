"""Referral foundation (Phase 14). Rewards are granted through the existing loyalty ledger — there is no
separate referral wallet. Basic, honestly-named safety rules only (no fraud detection is claimed):
self-referral is refused, and a customer can be the *referred* party at most once ever
(`uq_referral_events_referred_once`).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import ReferralEventStatus


class ReferralProgram(TimestampMixin, Base):
    """One per shop. Rewards are point amounts, granted via `loyalty_service` — the shop must have an active
    `LoyaltyProgram` for rewards to be grantable; if not, a referral can still be tracked but a reward stays
    "Not Available" until one is configured."""

    __tablename__ = "referral_programs"
    __table_args__ = (
        UniqueConstraint("shop_id"),
        UniqueConstraint("shop_id", "id"),
        positive("referrer_reward_points"),
        positive("referred_reward_points"),
        non_negative("min_purchase_amount"),
        positive("max_referrals_per_customer"),
        positive("expiry_days"),
        tenant_fk("created_by", "users"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    referrer_reward_points: Mapped[int | None] = mapped_column(Integer)
    referred_reward_points: Mapped[int | None] = mapped_column(Integer)
    min_purchase_amount: Mapped[Decimal | None] = mapped_column(Money)
    max_referrals_per_customer: Mapped[int | None] = mapped_column(Integer)
    expiry_days: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(IdType)


class ReferralCode(TimestampMixin, Base):
    __tablename__ = "referral_codes"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "code"),
        tenant_fk("customer_id", "customers"),
        Index("ix_referral_codes_shop_customer", "shop_id", "customer_id"),
        not_blank("code"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    customer_id: Mapped[int] = mapped_column(IdType)  # the referrer
    code: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class ReferralEvent(TimestampMixin, Base):
    """PENDING (referred customer recorded) -> QUALIFIED (the qualifying transaction happened) -> REWARDED
    (loyalty points granted to the referrer, and to the referred customer if configured). EXPIRED and
    INVALID never reward."""

    __tablename__ = "referral_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "referred_customer_id", name="uq_referral_events_referred_once"),
        tenant_fk("referral_code_id", "referral_codes"),
        tenant_fk("referrer_customer_id", "customers"),
        tenant_fk("referred_customer_id", "customers"),
        Index("ix_referral_events_shop_referrer", "shop_id", "referrer_customer_id"),
        Index("ix_referral_events_shop_status", "shop_id", "status"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    referral_code_id: Mapped[int] = mapped_column(IdType)
    referrer_customer_id: Mapped[int] = mapped_column(IdType)
    referred_customer_id: Mapped[int] = mapped_column(IdType)
    status: Mapped[ReferralEventStatus] = mapped_column(
        enum_type(ReferralEventStatus, "referral_event_status"), default=ReferralEventStatus.PENDING
    )
    qualifying_reference_type: Mapped[str | None] = mapped_column(String(20))  # SALE | QUICK_SALE
    qualifying_reference_id: Mapped[int | None] = mapped_column(IdType)
    qualified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rewarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
