"""Inbound webhooks from payment providers: verified, de-duplicated, applied once.

    POST /webhooks/{webhook_key}  ->  which integration? (the unguessable key)  ->  verify signature and timestamp (the adapter)
      ->  record the event (unique per integration and event id)  ->  apply it to the matching payment  ->  answer 200

The endpoint is public by design and safe because nothing is believed before the signature is checked with the shop's secret (an
environment variable named on the integration). A request with a bad signature, a stale timestamp or an unknown key creates NO event row
(so an attacker cannot fill the table); it is answered with a generic refusal and a security event is noted. A redelivery of an event that
was already PROCESSED is acknowledged and counted, never applied again; one that FAILED earlier is retried, because applying a status is
itself idempotent. Answers are 200 for everything the sender need not retry, so a provider does not hammer the endpoint.
"""

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import observability
from app.db.types import utc_now
from app.integrations import base, payments
from app.integrations.base import ProviderError
from app.models import Integration, OnlinePayment, WebhookEvent
from app.models.enums import EventSeverity, WebhookStatus
from app.services import integration_service, online_payment_service, system_event_service

MAX_BODY_BYTES = 64 * 1024


def _refuse(session: Session, key_known: bool, code: str, shop_id: int | None) -> tuple[int, dict[str, Any]]:
    system_event_service.record(
        session, category="security", severity=EventSeverity.WARNING, source="webhook", code=code,
        message="A webhook request was refused.", shop_id=shop_id,
    )  # fmt: skip
    observability.log_event("security", "webhook refused", code=code)
    return (401, {"status": "refused"}) if key_known else (404, {"status": "not_found"})


def receive(
    session: Session, webhook_key: str, headers: dict[str, str], body: bytes
) -> tuple[int, dict[str, Any]]:
    """Returns (HTTP status, body). Never raises for a bad request."""
    if len(body) > MAX_BODY_BYTES:
        return 413, {"status": "too_large"}
    row = (
        session.scalar(select(Integration).where(Integration.webhook_key == webhook_key))
        if 16 <= len(webhook_key) <= 64
        else None
    )
    spec = integration_service._spec(row) if row is not None else None  # noqa: SLF001
    if row is None or spec is None or not spec.webhook or not row.is_enabled:
        return _refuse(session, False, "unknown_webhook_key", None)
    secret = base.resolve_secret(row.webhook_credential_ref)
    provider = payments.PROVIDERS.get(row.provider)
    if secret is None or provider is None:
        return _refuse(session, False, "webhook_not_configured", row.shop_id)
    try:
        notice = provider().parse_webhook(headers, body, secret=secret)
    except ProviderError as error:
        if error.code == "invalid_payload":
            return 400, {"status": "invalid_payload"}
        return _refuse(session, True, error.code, row.shop_id)
    digest = hashlib.sha256(body).hexdigest()
    event = session.scalar(
        select(WebhookEvent).where(
            WebhookEvent.integration_id == row.id, WebhookEvent.event_id == notice.event_id
        )
    )
    if event is not None:
        if event.payload_hash != digest:
            return _refuse(session, True, "event_id_reused_with_different_body", row.shop_id)
        event.duplicates += 1
        if event.status not in (WebhookStatus.RECEIVED, WebhookStatus.FAILED):
            return 200, {"status": "duplicate", "event": event.status.value}
    else:
        event = WebhookEvent(
            shop_id=row.shop_id, integration_id=row.id, provider=row.provider, event_id=notice.event_id, event_type=notice.event_type,
            payload_hash=digest, received_at=utc_now(),
        )  # fmt: skip
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:  # another delivery of the same event got in first
            return 200, {"status": "duplicate"}
    event.status = WebhookStatus.PROCESSING
    try:
        with session.begin_nested():
            outcome, code = _apply(session, row, notice)
    except Exception:  # noqa: BLE001
        outcome, code = "FAILED", "processing_error"
        system_event_service.record(
            session,
            category="webhook",
            severity=EventSeverity.ERROR,
            source="webhook",
            code=code,
            message="A webhook could not be applied.",
            shop_id=row.shop_id,
        )
    event.status = {
        "APPLIED": WebhookStatus.PROCESSED,
        "IGNORED": WebhookStatus.IGNORED,
        "REVIEW": WebhookStatus.FAILED,
        "FAILED": WebhookStatus.FAILED,
    }[outcome]
    event.error_code = code
    event.processed_at = utc_now()
    integration_service.note_result(
        session,
        row,
        itype=row.integration_type,
        provider=row.provider,
        operation="webhook",
        ok=outcome != "FAILED",
        error_code=code if outcome == "FAILED" else None,
    )
    return 200, {"status": event.status.value.lower(), "event": notice.event_id}


def _apply(session: Session, row: Integration, notice: payments.WebhookNotice) -> tuple[str, str | None]:
    if notice.payment is None:
        return "IGNORED", "unsupported_event_type"
    payment = session.scalar(
        select(OnlinePayment).where(
            OnlinePayment.shop_id == row.shop_id, OnlinePayment.provider == row.provider, OnlinePayment.provider_txn_id == notice.payment.txn_id
        ).with_for_update()
    )  # fmt: skip
    if payment is None:
        return "IGNORED", "unknown_payment"
    result = online_payment_service.apply_status(
        session, payment, notice.payment, source="WEBHOOK", provider_event_id=notice.event_id
    )
    return result, "review_required" if result == "REVIEW" else None


def list_events(session: Session, shop_id: int, *, limit: int = 100) -> list[WebhookEvent]:
    return list(
        session.scalars(
            select(WebhookEvent)
            .where(WebhookEvent.shop_id == shop_id)
            .order_by(WebhookEvent.id.desc())
            .limit(min(limit, 500))
        )
    )
