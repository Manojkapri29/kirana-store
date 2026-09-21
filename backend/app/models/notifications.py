"""Notifications: events (what happened), deliveries (who was told, on which channel, and how it went) and preferences.

An EVENT is created once per real occurrence (`dedupe_key` makes a retried request or a repeated check harmless). A DELIVERY is
one channel to one person: IN_APP is delivered at once (the row IS the notification, with a read time); other channels stay
PENDING/RETRYING/SENT/FAILED and exist only when a provider for that channel is configured. Notification text carries no
amounts of money owed by a named person and no contact details: it says what happened and points at a record.
"""

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import DeliveryStatus, NotificationChannel


class NotificationEvent(CreatedAtMixin, Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "dedupe_key"),
        Index("ix_notification_events_shop_created", "shop_id", "created_at"),
        not_blank("event_type"),
        not_blank("title"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    event_type: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(30))
    dedupe_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(500))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(IdType)


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("event_id", "user_id", "channel"),
        tenant_fk("event_id", "notification_events"),
        tenant_fk("user_id", "users"),
        Index("ix_notification_deliveries_inbox", "shop_id", "user_id", "channel", "read_at"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
        non_negative("attempts"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    event_id: Mapped[int] = mapped_column(IdType)
    user_id: Mapped[int] = mapped_column(IdType)
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_type(NotificationChannel, "notification_channel")
    )
    status: Mapped[DeliveryStatus] = mapped_column(enum_type(DeliveryStatus, "delivery_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(30))
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # IN_APP only
    error_code: Mapped[str | None] = mapped_column(String(60))


class NotificationPreference(TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "user_id", "category"),
        tenant_fk("user_id", "users"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    user_id: Mapped[int] = mapped_column(IdType)
    category: Mapped[str] = mapped_column(String(30))
    in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[bool] = mapped_column(Boolean, default=False)
    sms: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    push: Mapped[bool] = mapped_column(Boolean, default=False)
