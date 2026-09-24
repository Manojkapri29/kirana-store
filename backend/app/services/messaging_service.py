"""Messages to customers (and report recipients) through an external channel, with honest outcomes.

Every attempt leaves a `message_deliveries` row saying what really happened:

    SENT | QUEUED (a temporary failure, will be retried) | FAILED | NOT_CONFIGURED ("Provider Not Configured")
    SKIPPED_NO_CONSENT (marketing, customer never opted in on that channel) | SKIPPED_NO_CONTACT (no email or phone on record)

Rules: MARKETING needs the customer's opt-in for that channel (the CRM consent flags; default is "not opted in"); TRANSACTIONAL messages
(order, payment, delivery, invoice) do not, but still need a contact detail and a configured provider. Nothing is ever recorded as sent unless a
provider accepted it. Sending is tried once and never repeated blindly: only a failure that could not have delivered anything (could not
connect, HTTP 429/5xx) becomes QUEUED, and the retry job (`process_due`) tries it again with backoff up to the notification attempt limit;
an unknown outcome (a timeout after sending) is FAILED, not repeated. An idempotency key makes a repeated request return the same row.
"""

import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.integrations.base import UNSAFE, ProviderError, mask_recipient
from app.models import Customer, MessageDelivery
from app.models.enums import IntegrationType, MessageKind, MessageStatus, NotificationChannel
from app.services import integration_service
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

CONSENT_FIELD = {
    NotificationChannel.EMAIL: "marketing_opt_in_email",
    NotificationChannel.SMS: "marketing_opt_in_sms",
    NotificationChannel.WHATSAPP: "marketing_opt_in_whatsapp",
    NotificationChannel.PUSH: "marketing_opt_in_push",
}
PURPOSES = (
    "ORDER_CONFIRMATION",
    "DELIVERY_UPDATE",
    "PAYMENT_CONFIRMATION",
    "INVOICE",
    "GENERAL",
    "CAMPAIGN",
    "REPORT",
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_BODY, MAX_SUBJECT = 1000, 150
NOT_CONFIGURED = "Provider Not Configured"


def _text(value: str | None, limit: int, field: str, *, required: bool = False) -> str:
    text = _CONTROL.sub("", (value or "").strip())
    if required and not text:
        raise InvalidInputError("Write the message.", field=field)
    if len(text) > limit:
        raise InvalidInputError(f"Keep it to {limit} characters.", field=field)
    return text


def _recipient(customer: Customer, channel: NotificationChannel) -> str | None:
    if channel is NotificationChannel.EMAIL:
        return customer.email
    if channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
        return customer.phone
    return None  # a push address is not stored on a customer


def get(session: Session, shop_id: int, message_id: int) -> MessageDelivery:
    row = session.scalar(
        select(MessageDelivery).where(MessageDelivery.shop_id == shop_id, MessageDelivery.id == message_id)
    )
    if row is None:
        raise NotFoundError("Message not found")
    return row


def list_messages(
    session: Session, shop_id: int, *, status: MessageStatus | None = None, limit: int = 100
) -> list[MessageDelivery]:
    query = select(MessageDelivery).where(MessageDelivery.shop_id == shop_id)
    if status is not None:
        query = query.where(MessageDelivery.status == status)
    return list(session.scalars(query.order_by(MessageDelivery.id.desc()).limit(min(limit, 500))))


def _attempt(session: Session, row: MessageDelivery, recipient: str, *, allow_queue: bool) -> None:
    found = integration_service.message_provider_for(session, row.shop_id, IntegrationType(row.channel.value))
    if found is None:
        row.status, row.error_code = MessageStatus.NOT_CONFIGURED, "not_configured"
        return
    integration, provider = found
    row.provider = provider.name
    row.attempts += 1
    try:
        integration_service.call(
            session, integration, provider.name, "send_message",
            lambda: provider.send(recipient=recipient, title=row.subject or "", message=row.body, timeout=10.0), kind=UNSAFE,
        )  # fmt: skip
    except ProviderError as error:
        row.error_code = error.code[:60]
        if error.retryable and allow_queue and row.attempts < get_settings().notification_max_attempts:
            delay = get_settings().notification_backoff_seconds * 2 ** (row.attempts - 1)
            row.status, row.next_attempt_at = MessageStatus.QUEUED, utc_now() + timedelta(seconds=delay)
        else:
            row.status, row.next_attempt_at = MessageStatus.FAILED, None
        return
    row.status, row.sent_at, row.error_code, row.next_attempt_at = MessageStatus.SENT, utc_now(), None, None


def send_to_customer(
    session: Session, ctx: RequestContext, *, customer_id: int, channel: NotificationChannel, kind: MessageKind, purpose: str, body: str,
    subject: str | None = None, idempotency_key: str | None = None, campaign_id: int | None = None, allow_queue: bool = True,
) -> MessageDelivery:  # fmt: skip
    if channel is NotificationChannel.IN_APP:
        raise InvalidInputError("Choose EMAIL, SMS, WHATSAPP or PUSH.", field="channel")
    if purpose not in PURPOSES:
        raise InvalidInputError(f"Purpose must be one of: {', '.join(PURPOSES)}.", field="purpose")
    if kind is MessageKind.MARKETING and purpose not in ("CAMPAIGN", "GENERAL"):
        raise InvalidInputError("A marketing message is a campaign or a general message.", field="purpose")
    text = _text(body, MAX_BODY, "body", required=True)
    title = _text(subject, MAX_SUBJECT, "subject")
    if idempotency_key:
        existing = session.scalar(
            select(MessageDelivery).where(
                MessageDelivery.shop_id == ctx.shop_id, MessageDelivery.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (existing.customer_id, existing.channel, existing.body, existing.kind) != (
                customer_id,
                channel,
                text,
                kind,
            ):
                raise ConflictError(
                    "This request key was already used for a different message.",
                    code="idempotency_key_reused",
                )
            return existing
    customer = session.scalar(
        select(Customer).where(Customer.shop_id == ctx.shop_id, Customer.id == customer_id)
    )
    if customer is None:
        raise NotFoundError("Customer not found")
    recipient = _recipient(customer, channel)
    row = MessageDelivery(
        shop_id=ctx.shop_id, customer_id=customer_id, channel=channel, kind=kind, purpose=purpose, status=MessageStatus.QUEUED, subject=title or None,
        body=text, idempotency_key=idempotency_key, campaign_id=campaign_id, created_by=ctx.user_id,
        recipient_masked=mask_recipient(recipient) if recipient else None,
    )  # fmt: skip
    session.add(row)
    if kind is MessageKind.MARKETING and not getattr(customer, CONSENT_FIELD[channel]):
        row.status, row.error_code = MessageStatus.SKIPPED_NO_CONSENT, "no_consent"
    elif (
        integration_service.message_provider_for(session, ctx.shop_id, IntegrationType(channel.value)) is None
    ):
        row.status, row.error_code = (
            MessageStatus.NOT_CONFIGURED,
            "not_configured",
        )  # said first: nothing could be sent to anyone
    elif not recipient:
        row.status, row.error_code = MessageStatus.SKIPPED_NO_CONTACT, "no_contact"
    else:
        _attempt(session, row, recipient, allow_queue=allow_queue)
    session.flush()
    return row


def send_report(
    session: Session, shop_id: int, user_id: int, recipients: list[str], subject: str, body: str, key: str
) -> str:
    """Email a scheduled report's summary to its recipients. Returns SENT (all accepted), FAILED, or NOT_CONFIGURED. One delivery row per
    recipient, keyed so a repeated run never mails the same report twice."""
    if integration_service.message_provider_for(session, shop_id, IntegrationType.EMAIL) is None:
        return MessageStatus.NOT_CONFIGURED.value
    statuses = []
    for address in recipients:
        token = f"{key}:{hashlib.sha256(address.encode()).hexdigest()[:16]}"
        row = session.scalar(
            select(MessageDelivery).where(
                MessageDelivery.shop_id == shop_id, MessageDelivery.idempotency_key == token
            )
        )
        if row is None:
            row = MessageDelivery(
                shop_id=shop_id, channel=NotificationChannel.EMAIL, kind=MessageKind.TRANSACTIONAL, purpose="REPORT", status=MessageStatus.QUEUED,
                subject=_text(subject, MAX_SUBJECT, "subject"), body=_text(body, MAX_BODY * 4, "body"), idempotency_key=token, created_by=user_id,
                recipient_masked=mask_recipient(address),
            )  # fmt: skip
            session.add(row)
            _attempt(session, row, address, allow_queue=False)
            session.flush()
        statuses.append(row.status)
    if statuses and all(s is MessageStatus.SENT for s in statuses):
        return MessageStatus.SENT.value
    return MessageStatus.FAILED.value


def process_due(session: Session, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Try QUEUED messages again (temporary failures only). Called by the background job. The recipient is looked up again from the customer."""
    now = now or utc_now()
    rows = session.scalars(
        select(MessageDelivery)
        .where(MessageDelivery.status == MessageStatus.QUEUED, MessageDelivery.next_attempt_at <= now)
        .order_by(MessageDelivery.id)
        .limit(limit)
    ).all()
    counts = {"attempted": 0, "sent": 0, "queued": 0, "failed": 0}
    for row in rows:
        customer = (
            session.scalar(
                select(Customer).where(Customer.shop_id == row.shop_id, Customer.id == row.customer_id)
            )
            if row.customer_id
            else None
        )
        recipient = _recipient(customer, row.channel) if customer else None
        if recipient is None:
            row.status, row.error_code, row.next_attempt_at = (
                MessageStatus.SKIPPED_NO_CONTACT,
                "no_contact",
                None,
            )
            continue
        if row.kind is MessageKind.MARKETING and not getattr(customer, CONSENT_FIELD[row.channel]):
            row.status, row.error_code, row.next_attempt_at = (
                MessageStatus.SKIPPED_NO_CONSENT,
                "no_consent",
                None,
            )  # consent withdrawn since
            continue
        counts["attempted"] += 1
        _attempt(session, row, recipient, allow_queue=True)
        counts[{MessageStatus.SENT: "sent", MessageStatus.QUEUED: "queued"}.get(row.status, "failed")] += 1
    session.flush()
    return counts
