"""Campaign management (Phase 14): a marketing campaign targets a customer group or an ad-hoc segment, may
carry an existing `Promotion` (never a second discount system), and is only ever "sent" through the same
channel-provider abstraction `notification_service` already has. Since no external provider is configured
anywhere in this codebase, every non-in-app send is honestly recorded as `NOT_CONFIGURED` — nothing here
pretends to deliver a message it cannot.

`CampaignAudienceSnapshot` freezes who was targeted at launch time, so a report about a past campaign never
drifts if group membership changes afterwards. `CampaignSend` freezes the outcome per customer per channel.
"""

from datetime import datetime

from sqlalchemy import Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import CampaignSendStatus, CampaignStatus, NotificationChannel


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("target_group_id", "customer_groups"),
        tenant_fk("promotion_id", "promotions"),
        tenant_fk("created_by", "users"),
        tenant_fk("launched_by", "users"),
        tenant_fk("cancelled_by", "users"),
        Index("ix_campaigns_shop_status", "shop_id", "status"),
        not_blank("name"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CampaignStatus] = mapped_column(
        enum_type(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT
    )
    channel: Mapped[NotificationChannel] = mapped_column(enum_type(NotificationChannel, "channel"))
    # Exactly one of these names the audience; a saved group is preferred (it can be reused and recalculated),
    # a bare segment value (e.g. "INACTIVE") is for a one-off campaign with no saved group.
    target_group_id: Mapped[int | None] = mapped_column(IdType)
    target_segment: Mapped[str | None] = mapped_column(String(40))
    promotion_id: Mapped[int | None] = mapped_column(IdType)
    message_template: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int] = mapped_column(IdType)
    launched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    launched_by: Mapped[int | None] = mapped_column(IdType)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by: Mapped[int | None] = mapped_column(IdType)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    # True while a launch is waiting on a separate approval (audience over the shop's threshold).
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false())


class CampaignAudienceSnapshot(CreatedAtMixin, Base):
    """Who was targeted, frozen at launch. INSERT-ONLY."""

    __tablename__ = "campaign_audience_snapshots"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "campaign_id", "customer_id"),
        tenant_fk("campaign_id", "campaigns"),
        tenant_fk("customer_id", "customers"),
        Index("ix_campaign_audience_shop_campaign", "shop_id", "campaign_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    campaign_id: Mapped[int] = mapped_column(IdType)
    customer_id: Mapped[int] = mapped_column(IdType)


class CampaignSend(CreatedAtMixin, Base):
    """One attempted delivery to one customer, on one channel. INSERT-ONLY: the honest outcome of that one
    attempt, never rewritten. `detail` explains the status in plain words (e.g. which provider is missing, or
    which consent was absent)."""

    __tablename__ = "campaign_sends"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "campaign_id", "customer_id"),
        tenant_fk("campaign_id", "campaigns"),
        tenant_fk("customer_id", "customers"),
        Index("ix_campaign_sends_shop_campaign", "shop_id", "campaign_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    campaign_id: Mapped[int] = mapped_column(IdType)
    customer_id: Mapped[int] = mapped_column(IdType)
    channel: Mapped[NotificationChannel] = mapped_column(enum_type(NotificationChannel, "channel"))
    status: Mapped[CampaignSendStatus] = mapped_column(enum_type(CampaignSendStatus, "campaign_send_status"))
    detail: Mapped[str | None] = mapped_column(String(300))
