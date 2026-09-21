"""Plans and shop subscriptions (a foundation: no payment processing exists yet).

  plans                the catalogue: name, price, billing interval. Shared reference data (like business
                       types), configurable by data; nothing about a plan is hard-coded in the code.
  plan_features        what each plan allows: a yes/no feature, or a numeric limit (NULL limit = unlimited).
  shop_subscriptions   which plan a shop is on, and for how long. History is kept: a change adds a row.
  subscription_usage   metered counters per shop and calendar month (invoices posted, price lookups made).

`entitlement_service` is the only place that answers "may this shop do this?".
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, UTCDateTime
from app.models.base import (
    Base,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    shop_id_column,
)
from app.models.enums import BillingInterval, SubscriptionStatus


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("code"), not_blank("code"), not_blank("name"), non_negative("price"))

    id: Mapped[int] = id_column()
    code: Mapped[str] = mapped_column(String(30))  # stable identifier, e.g. "free"
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    # NULL means "not published / contact us". Currency is stored, not assumed (India-focused default INR).
    price: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    billing_interval: Mapped[BillingInterval] = mapped_column(
        enum_type(BillingInterval, "billing_interval"), default=BillingInterval.MONTHLY
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    sort_order: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class PlanFeature(Base):
    """One entry of a plan: a feature switch (`enabled`) or a numeric limit (`limit_value`, NULL = no cap)."""

    __tablename__ = "plan_features"
    __table_args__ = (
        UniqueConstraint("plan_id", "feature_key"),
        not_blank("feature_key"),
        non_negative("limit_value"),
    )

    id: Mapped[int] = id_column()
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    feature_key: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    limit_value: Mapped[int | None] = mapped_column(BigInteger)


class ShopSubscription(TimestampMixin, Base):
    __tablename__ = "shop_subscriptions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        # A shop has at most one current (TRIAL or ACTIVE) subscription.
        Index(
            "uq_shop_subscriptions_current",
            "shop_id",
            unique=True,
            sqlite_where=text("status IN ('TRIAL', 'ACTIVE')"),
            postgresql_where=text("status IN ('TRIAL', 'ACTIVE')"),
        ),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[SubscriptionStatus] = mapped_column(enum_type(SubscriptionStatus, "status"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # NULL = no end date
    notes: Mapped[str | None] = mapped_column(Text)


class SubscriptionUsage(TimestampMixin, Base):
    __tablename__ = "subscription_usage"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),  # like every shop-owned table: a target for tenant foreign keys
        UniqueConstraint("shop_id", "period", "metric"),
        non_negative("count"),
        not_blank("metric"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    period: Mapped[str] = mapped_column(String(7))  # calendar month in the shop's timezone, "2026-09"
    metric: Mapped[str] = mapped_column(String(50))
    count: Mapped[int] = mapped_column(BigInteger, default=0)
