from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    AccountingMapping,
    IntegrationEvent,
    MessageDelivery,
    OnlinePayment,
    OnlinePaymentEvent,
    WebhookEvent,
)
from app.models.enums import MessageKind, NotificationChannel, OnlinePaymentMethod, OnlinePaymentPurpose


class ConfigureIn(BaseModel):
    """Provider and NON-SECRET settings. Credentials are never accepted here: give the NAME of an environment variable."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(max_length=40)
    config: dict[str, Any] = Field(default_factory=dict)
    credential_ref: str | None = Field(default=None, max_length=100)
    webhook_credential_ref: str | None = Field(default=None, max_length=100)
    is_enabled: bool | None = None


class RotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_ref: str | None = Field(default=None, max_length=100)
    webhook_credential_ref: str | None = Field(default=None, max_length=100)


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: OnlinePaymentMethod
    purpose: OnlinePaymentPurpose
    amount: Decimal
    sale_id: int | None = None
    quick_sale_id: int | None = None
    customer_id: int | None = None
    order_ref: str | None = Field(default=None, max_length=60)
    provider_txn_id: str | None = Field(default=None, max_length=100)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=200)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal


class PaymentOut(BaseModel):
    id: int
    provider: str
    method: str
    purpose: str
    status: str
    amount: str
    refunded_amount: str
    currency: str
    sale_id: int | None
    quick_sale_id: int | None
    customer_id: int | None
    order_ref: str | None
    provider_txn_id: str | None
    needs_review: bool
    review_reason: str | None
    failure_code: str | None
    khata_entry_id: int | None
    captured_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, p: OnlinePayment) -> "PaymentOut":
        return cls(
            id=p.id, provider=p.provider, method=p.method.value, purpose=p.purpose.value, status=p.status.value, amount=str(p.amount),
            refunded_amount=str(p.refunded_amount), currency=p.currency, sale_id=p.sale_id, quick_sale_id=p.quick_sale_id, customer_id=p.customer_id,
            order_ref=p.order_ref, provider_txn_id=p.provider_txn_id, needs_review=p.needs_review, review_reason=p.review_reason,
            failure_code=p.failure_code, khata_entry_id=p.khata_entry_id, captured_at=p.captured_at, created_at=p.created_at,
        )  # fmt: skip


class PaymentListOut(BaseModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int


class PaymentEventOut(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    source: str
    provider_event_id: str | None
    note: str | None
    created_at: datetime

    @classmethod
    def of(cls, e: OnlinePaymentEvent) -> "PaymentEventOut":
        return cls(
            id=e.id,
            from_status=e.from_status,
            to_status=e.to_status,
            source=e.source,
            provider_event_id=e.provider_event_id,
            note=e.note,
            created_at=e.created_at,
        )


class IntegrationEventOut(BaseModel):
    id: int
    integration_type: str
    provider: str
    operation: str
    outcome: str
    error_code: str | None
    duration_ms: int
    created_at: datetime

    @classmethod
    def of(cls, e: IntegrationEvent) -> "IntegrationEventOut":
        return cls(
            id=e.id,
            integration_type=e.integration_type.value,
            provider=e.provider,
            operation=e.operation,
            outcome=e.outcome,
            error_code=e.error_code,
            duration_ms=e.duration_ms,
            created_at=e.created_at,
        )


class WebhookEventOut(BaseModel):
    id: int
    provider: str
    event_id: str
    event_type: str
    status: str
    error_code: str | None
    duplicates: int
    received_at: datetime
    processed_at: datetime | None

    @classmethod
    def of(cls, e: WebhookEvent) -> "WebhookEventOut":
        return cls(
            id=e.id,
            provider=e.provider,
            event_id=e.event_id,
            event_type=e.event_type,
            status=e.status.value,
            error_code=e.error_code,
            duplicates=e.duplicates,
            received_at=e.received_at,
            processed_at=e.processed_at,
        )


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    channel: NotificationChannel
    kind: MessageKind
    purpose: str = Field(max_length=30)
    subject: str | None = Field(default=None, max_length=150)
    body: str = Field(max_length=1000)


class MessageOut(BaseModel):
    id: int
    customer_id: int | None
    channel: str
    kind: str
    purpose: str
    provider: str | None
    status: str
    message: str
    attempts: int
    error_code: str | None
    recipient: str | None
    created_at: datetime
    sent_at: datetime | None

    @classmethod
    def of(cls, m: MessageDelivery) -> "MessageOut":
        words = {
            "SENT": "Sent",
            "QUEUED": "Waiting to be retried",
            "FAILED": "Not delivered",
            "NOT_CONFIGURED": "Provider Not Configured",
            "SKIPPED_NO_CONSENT": "Not sent: the customer has not opted in",
            "SKIPPED_NO_CONTACT": "Not sent: no contact detail",
        }
        return cls(
            id=m.id, customer_id=m.customer_id, channel=m.channel.value, kind=m.kind.value, purpose=m.purpose, provider=m.provider, status=m.status.value,
            message=words[m.status.value], attempts=m.attempts, error_code=m.error_code, recipient=m.recipient_masked, created_at=m.created_at, sent_at=m.sent_at,
        )  # fmt: skip


class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(max_length=60)
    external_code: str = Field(max_length=60)
    external_name: str | None = Field(default=None, max_length=120)


class MappingClearIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(max_length=60)


class MappingOut(BaseModel):
    source_key: str
    external_code: str
    external_name: str | None

    @classmethod
    def of(cls, m: AccountingMapping) -> "MappingOut":
        return cls(source_key=m.source_key, external_code=m.external_code, external_name=m.external_name)


class DistanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat1: Decimal = Field(ge=-90, le=90)
    lon1: Decimal = Field(ge=-180, le=180)
    lat2: Decimal = Field(ge=-90, le=90)
    lon2: Decimal = Field(ge=-180, le=180)


class GeocodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(max_length=300)


_ = date
