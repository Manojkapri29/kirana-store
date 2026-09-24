"""Integrations (Phase 17): the per-shop configuration of each external service, a safe call log, online payment records, verified
webhook events, customer message deliveries and accounting-code mappings.

Secrets are never stored here. An integration holds only the NAME of an environment variable (`credential_ref`); the value is read
from the environment at the moment of use and never returned, logged or written to a row.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, UTCDateTime
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
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    MessageKind,
    MessageStatus,
    NotificationChannel,
    OnlinePaymentMethod,
    OnlinePaymentPurpose,
    OnlinePaymentStatus,
    WebhookStatus,
)


class Integration(TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "integration_type"),
        UniqueConstraint("webhook_key"),
        tenant_fk("created_by", "users"),
        not_blank("provider"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    integration_type: Mapped[IntegrationType] = mapped_column(enum_type(IntegrationType, "integration_type"))
    provider: Mapped[str] = mapped_column(String(40))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false())
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus, "integration_status"), default=IntegrationStatus.NOT_CONFIGURED
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # non-secret settings only
    credential_ref: Mapped[str | None] = mapped_column(String(100))  # the NAME of an environment variable
    webhook_key: Mapped[str | None] = mapped_column(
        String(64)
    )  # the public, unguessable part of the webhook URL
    webhook_credential_ref: Mapped[str | None] = mapped_column(String(100))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # consecutive
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int] = mapped_column(IdType)


class IntegrationEvent(CreatedAtMixin, Base):
    """One external call, without its payload: what was asked, whether it worked, how long it took, and a safe error code."""

    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        Index("ix_integration_events_shop_created", "shop_id", "created_at"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    integration_type: Mapped[IntegrationType] = mapped_column(enum_type(IntegrationType, "integration_type"))
    provider: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(10))  # SUCCESS | FAILURE
    error_code: Mapped[str | None] = mapped_column(String(60))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class OnlinePayment(TimestampMixin, Base):
    """Evidence of a payment made through an external provider. It is never a second ledger: a SALE_PAYMENT posts nothing; a
    KHATA_PAYMENT is recorded once, through `khata_service`, when it is captured."""

    __tablename__ = "online_payments"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "provider", "provider_txn_id"),
        UniqueConstraint("shop_id", "idempotency_key"),
        tenant_fk("sale_id", "sales"),
        tenant_fk("quick_sale_id", "quick_sales"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("created_by", "users"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("refunded_amount >= 0 AND refunded_amount <= amount", name="refund_within_amount"),
        Index("ix_online_payments_shop_status", "shop_id", "status"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    provider: Mapped[str] = mapped_column(String(40))
    method: Mapped[OnlinePaymentMethod] = mapped_column(
        enum_type(OnlinePaymentMethod, "online_payment_method")
    )
    purpose: Mapped[OnlinePaymentPurpose] = mapped_column(
        enum_type(OnlinePaymentPurpose, "online_payment_purpose")
    )
    status: Mapped[OnlinePaymentStatus] = mapped_column(
        enum_type(OnlinePaymentStatus, "online_payment_status"), default=OnlinePaymentStatus.CREATED
    )
    amount: Mapped[Decimal] = mapped_column(Money)
    refunded_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    sale_id: Mapped[int | None] = mapped_column(IdType)
    quick_sale_id: Mapped[int | None] = mapped_column(IdType)
    customer_id: Mapped[int | None] = mapped_column(IdType)
    order_ref: Mapped[str | None] = mapped_column(
        String(60)
    )  # there is no orders module: a free reference only
    provider_txn_id: Mapped[str | None] = mapped_column(String(100))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false())
    review_reason: Mapped[str | None] = mapped_column(String(200))
    khata_entry_id: Mapped[int | None] = mapped_column(IdType)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_code: Mapped[str | None] = mapped_column(String(60))
    created_by: Mapped[int] = mapped_column(IdType)


class OnlinePaymentEvent(CreatedAtMixin, Base):
    """Every status change of a payment, and who or what caused it. Insert-only."""

    __tablename__ = "online_payment_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("payment_id", "online_payments"),
        Index("ix_online_payment_events_payment", "shop_id", "payment_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    payment_id: Mapped[int] = mapped_column(IdType)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(12))  # WEBHOOK | VERIFY | MANUAL | SYSTEM | REFUND
    provider_event_id: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(200))


class WebhookEvent(TimestampMixin, Base):
    """A webhook whose signature was verified. (event id) is unique per integration, so a redelivery is recognised, never re-applied."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("integration_id", "event_id"),
        tenant_fk("integration_id", "integrations"),
        Index("ix_webhook_events_shop_received", "shop_id", "received_at"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    integration_id: Mapped[int] = mapped_column(IdType)
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[WebhookStatus] = mapped_column(
        enum_type(WebhookStatus, "webhook_status"), default=WebhookStatus.RECEIVED
    )
    payload_hash: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(60))
    duplicates: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class MessageDelivery(TimestampMixin, Base):
    """One message to one recipient through an external channel, and what honestly happened to it."""

    __tablename__ = "message_deliveries"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "idempotency_key"),
        tenant_fk("customer_id", "customers"),
        Index("ix_message_deliveries_shop_status", "shop_id", "status"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    customer_id: Mapped[int | None] = mapped_column(IdType)
    channel: Mapped[NotificationChannel] = mapped_column(enum_type(NotificationChannel, "message_channel"))
    kind: Mapped[MessageKind] = mapped_column(enum_type(MessageKind, "message_kind"))
    purpose: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[MessageStatus] = mapped_column(enum_type(MessageStatus, "message_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error_code: Mapped[str | None] = mapped_column(String(60))
    recipient_masked: Mapped[str | None] = mapped_column(String(80))
    subject: Mapped[str | None] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    campaign_id: Mapped[int | None] = mapped_column(IdType)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int | None] = mapped_column(IdType)


class AccountingMapping(TimestampMixin, Base):
    """Which code in an outside accounting system a kind of record should carry. Purely a label on an export."""

    __tablename__ = "accounting_mappings"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "source_key"),
        not_blank("source_key"),
        not_blank("external_code"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    source_key: Mapped[str] = mapped_column(String(60))
    external_code: Mapped[str] = mapped_column(String(60))
    external_name: Mapped[str | None] = mapped_column(String(120))
