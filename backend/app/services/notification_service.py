"""Notifications: provider-independent, in-app first, and never able to fail a business transaction.

    something happens -> emit(event) once (de-duplicated) -> deliveries per person and channel, respecting preferences
      IN_APP    delivered immediately: the delivery row IS the notification (unread until read_at is set).
      EMAIL / SMS / WHATSAPP / PUSH   exist only when a provider for that channel is configured. Until then a person may
                                      switch them on in preferences, but nothing is sent and nothing pretends to be.

Business code calls `emit_safely`: it runs in a savepoint and swallows any problem (logging it and noting a system event), so
an order, a sale or a payment is never rolled back because a notification could not be written. External delivery is a
separate step (`process_due`) with a timeout-aware provider interface, exponential backoff between attempts and a limit on
attempts; a provider that keeps failing ends as FAILED with a safe code and a system event, never an exception.

Notification text says what happened and points at a record. It carries no phone number, no address and no amount owed by a
named person.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import observability
from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.integrations.base import ProviderError
from app.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationPreference,
    Shop,
    User,
)
from app.models.enums import DeliveryStatus, EventSeverity, NotificationChannel
from app.services import system_event_service
from app.services.errors import InvalidInputError, NotFoundError

EVENT_CATEGORY = {
    "ONLINE_ORDER_PLACED": "ONLINE_ORDERS",
    "ONLINE_ORDER_ACCEPTED": "ONLINE_ORDERS",
    "ONLINE_ORDER_REJECTED": "ONLINE_ORDERS",
    "ONLINE_ORDER_READY": "ONLINE_ORDERS",
    "ONLINE_ORDER_OUT_FOR_DELIVERY": "ONLINE_ORDERS",
    "ONLINE_ORDER_DELIVERED": "ONLINE_ORDERS",
    "LOW_STOCK": "LOW_STOCK",
    "PAYMENT_RECEIVED": "PAYMENTS",
    "KHATA_REMINDER": "KHATA_REMINDERS",
    "BACKUP_FAILED": "BACKUP",
    "BACKUP_COMPLETED": "BACKUP",
    "AI_USAGE_LIMIT": "AI_USAGE",
    "SUBSCRIPTION_LIMIT": "SUBSCRIPTION",
    "BUSINESS_ALERT": "ALERTS",
    "STOCK_COUNT_VARIANCE": "OPERATIONS",
    "REORDER_RECOMMENDATION": "OPERATIONS",
    "PURCHASE_DRAFT_READY": "OPERATIONS",
    "APPROVAL_REQUESTED": "OPERATIONS",
    "TASK_ASSIGNED": "TASKS",
    "TASK_DUE": "TASKS",
    "SCHEDULED_REPORT_READY": "REPORTS",
}
CATEGORIES = (
    "LOW_STOCK",
    "ONLINE_ORDERS",
    "PAYMENTS",
    "KHATA_REMINDERS",
    "BACKUP",
    "AI_USAGE",
    "SUBSCRIPTION",
    "ALERTS",
    "OPERATIONS",
    "TASKS",
    "REPORTS",
)
EXTERNAL = (
    NotificationChannel.EMAIL,
    NotificationChannel.SMS,
    NotificationChannel.WHATSAPP,
    NotificationChannel.PUSH,
)
_PREFERENCE_FIELD = {
    NotificationChannel.IN_APP: "in_app",
    NotificationChannel.EMAIL: "email",
    NotificationChannel.SMS: "sms",
    NotificationChannel.WHATSAPP: "whatsapp",
    NotificationChannel.PUSH: "push",
}
_PROVIDER_SETTING = {
    NotificationChannel.EMAIL: "notification_email_provider",
    NotificationChannel.SMS: "notification_sms_provider",
    NotificationChannel.WHATSAPP: "notification_whatsapp_provider",
    NotificationChannel.PUSH: "notification_push_provider",
}


# --- Providers (none is written; this is the seam) ---------------------------------------------------------------


class NotificationProvider(Protocol):
    name: str

    def send(self, *, recipient: str | None, title: str, message: str, timeout: float) -> None:
        """Deliver one message or raise `ProviderError`. Must honour `timeout` and never block longer."""


# channel -> provider name -> factory. Empty on purpose: paid or external services are added deliberately, one at a time.
PROVIDERS: dict[NotificationChannel, dict[str, type]] = {channel: {} for channel in EXTERNAL}


def provider_for(
    channel: NotificationChannel, settings: Settings | None = None
) -> NotificationProvider | None:
    """The configured provider for a channel, or None ("not configured")."""
    settings = settings or get_settings()
    name = getattr(settings, _PROVIDER_SETTING[channel], None) if channel in _PROVIDER_SETTING else None
    factory = PROVIDERS.get(channel, {}).get(name or "")
    return factory() if factory else None


def channel_status(
    settings: Settings | None = None, session: Session | None = None, shop_id: int | None = None
) -> dict[str, bool]:
    """Which channels can actually deliver: for the preferences screen to be honest about it. With a session and shop it also counts
    the provider that shop configured (Phase 17 integrations)."""
    status = {NotificationChannel.IN_APP.value: True}
    for channel in EXTERNAL:
        shop_provider = _shop_provider(session, shop_id, channel) if session is not None and shop_id is not None else None
        status[channel.value] = provider_for(channel, settings) is not None or shop_provider is not None
    return status


# --- Emitting -----------------------------------------------------------------------------------------------------


def _preference(session: Session, shop_id: int, user_id: int, category: str) -> NotificationPreference | None:
    return session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.shop_id == shop_id,
            NotificationPreference.user_id == user_id,
            NotificationPreference.category == category,
        )
    )


def _wants(pref: NotificationPreference | None, channel: NotificationChannel) -> bool:
    if pref is None:
        return channel is NotificationChannel.IN_APP  # by default people get in-app notifications only
    return bool(getattr(pref, _PREFERENCE_FIELD[channel]))


def emit(
    session: Session,
    shop_id: int,
    event_type: str,
    *,
    title: str,
    message: str,
    dedupe_key: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    settings: Settings | None = None,
) -> NotificationEvent:
    """Create the event once and deliver it. A second call with the same `dedupe_key` changes nothing and returns the event."""
    if event_type not in EVENT_CATEGORY:
        raise InvalidInputError("Unknown notification type.", field="event_type")
    settings = settings or get_settings()
    existing = session.scalar(
        select(NotificationEvent).where(
            NotificationEvent.shop_id == shop_id, NotificationEvent.dedupe_key == dedupe_key
        )
    )
    if existing is not None:
        return existing
    category = EVENT_CATEGORY[event_type]
    event = NotificationEvent(
        shop_id=shop_id, event_type=event_type, category=category, dedupe_key=dedupe_key[:120],
        title=title[:200], message=message[:500], entity_type=entity_type, entity_id=entity_id,
    )  # fmt: skip
    session.add(event)
    session.flush()
    now = utc_now()
    users = session.scalars(select(User).where(User.shop_id == shop_id, User.is_active.is_(True)))
    for user in users:
        pref = _preference(session, shop_id, user.id, category)
        if _wants(pref, NotificationChannel.IN_APP):
            session.add(
                NotificationDelivery(
                    shop_id=shop_id, event_id=event.id, user_id=user.id, channel=NotificationChannel.IN_APP,
                    status=DeliveryStatus.SENT, attempts=1, sent_at=now, last_attempt_at=now,
                )
            )  # fmt: skip
        for channel in EXTERNAL:
            if _wants(pref, channel) and (
                provider_for(channel, settings) is not None or _shop_provider(session, shop_id, channel) is not None
            ):
                session.add(
                    NotificationDelivery(
                        shop_id=shop_id, event_id=event.id, user_id=user.id, channel=channel,
                        status=DeliveryStatus.PENDING, attempts=0, next_attempt_at=now,
                    )
                )  # fmt: skip
    session.flush()
    return event


def emit_safely(
    session: Session, shop_id: int, event_type: str, **kwargs: object
) -> NotificationEvent | None:
    """`emit` for business code: any problem is contained in a savepoint, logged and noted, never raised."""
    if not get_settings().feature_notifications:
        return None
    try:
        with session.begin_nested():
            return emit(session, shop_id, event_type, **kwargs)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        observability.log_event(
            "notification",
            "could not create a notification",
            level=logging.WARNING,
            event_type=event_type,
            shop_id=shop_id,
        )
        system_event_service.record(
            session, category="notification", severity=EventSeverity.WARNING, source="emit",
            code="emit_failed", message=f"A {event_type} notification could not be created.", shop_id=shop_id,
        )  # fmt: skip
        return None


def emit_platform_event(
    session: Session, event_type: str, title: str, message: str, *, dedupe_suffix: str
) -> int:
    """Tell every active shop about something platform-wide (a failed backup). Returns how many shops were told."""
    told = 0
    for shop_id in session.scalars(select(Shop.id).where(Shop.account_status.in_(["ACTIVE", "TRIAL"]))):
        if emit_safely(
            session,
            shop_id,
            event_type,
            title=title,
            message=message,
            dedupe_key=f"{event_type}:{dedupe_suffix}",
        ):
            told += 1
    return told


def emit_low_stock_after_sale(session: Session, shop_id: int, product_ids: list[int]) -> None:
    """After stock went out: tell the owner about products now at or below their reorder level (once per product per day)."""
    from app.models import Product
    from app.services import inventory_service

    if not product_ids:
        return
    try:
        with session.begin_nested():
            stock = inventory_service.get_stock_map(session, shop_id, product_ids)
            today = utc_now().strftime("%Y%m%d")
            for product in session.scalars(
                select(Product).where(Product.shop_id == shop_id, Product.id.in_(product_ids))
            ):
                current = stock.get(product.id)
                if current is None or product.reorder_level <= 0 and current > 0:
                    continue
                if current <= product.reorder_level:
                    state = "out" if current <= 0 else "low"
                    text = (
                        f"{product.name} is out of stock."
                        if state == "out"
                        else f"{product.name} is below its reorder level."
                    )
                    emit_safely(
                        session, shop_id, "LOW_STOCK", title="Stock alert", message=text,
                        dedupe_key=f"low:{product.id}:{state}:{today}", entity_type="product", entity_id=product.id,
                    )  # fmt: skip
    except Exception:  # noqa: BLE001
        observability.log_event(
            "notification", "low-stock check failed", level=logging.WARNING, shop_id=shop_id
        )


# --- Delivery of non-in-app channels ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryRun:
    attempted: int
    sent: int
    retrying: int
    failed: int


def _shop_provider(session: Session, shop_id: int, channel: NotificationChannel):  # noqa: ANN202
    """The provider this SHOP configured for the channel (Phase 17 integrations), or None."""
    from app.models.enums import IntegrationType
    from app.services import integration_service

    found = integration_service.message_provider_for(session, shop_id, IntegrationType(channel.value))
    return found[1] if found else None


def _recipient_of(user: User, channel: NotificationChannel) -> str | None:
    if channel is NotificationChannel.EMAIL:
        return user.email
    if channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
        return user.phone
    return f"user:{user.id}" if channel is NotificationChannel.PUSH else None


def process_due(
    session: Session,
    *,
    now: datetime | None = None,
    providers: Mapping[NotificationChannel, NotificationProvider] | None = None,
    settings: Settings | None = None,
    limit: int = 100,
) -> DeliveryRun:
    """Attempt due external deliveries once. Failures back off exponentially and end as FAILED after the attempt limit."""
    settings = settings or get_settings()
    now = now or utc_now()
    rows = session.scalars(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
            NotificationDelivery.channel != NotificationChannel.IN_APP,
            NotificationDelivery.next_attempt_at <= now,
        )
        .order_by(NotificationDelivery.id)
        .limit(limit)
    ).all()
    attempted = sent = retrying = failed = 0
    for delivery in rows:
        provider = (
            (providers or {}).get(delivery.channel)
            or _shop_provider(session, delivery.shop_id, delivery.channel)
            or provider_for(delivery.channel, settings)
        )
        event = session.get(NotificationEvent, delivery.event_id)
        user = session.get(User, delivery.user_id)
        if provider is None or event is None or user is None:
            delivery.status, delivery.error_code = DeliveryStatus.CANCELLED, "not_configured"
            continue
        attempted += 1
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.provider = provider.name
        try:
            provider.send(
                recipient=_recipient_of(user, delivery.channel),
                title=event.title, message=event.message, timeout=10.0,
            )  # fmt: skip
            delivery.status, delivery.sent_at, delivery.error_code, delivery.next_attempt_at = (
                DeliveryStatus.SENT,
                now,
                None,
                None,
            )
            sent += 1
        except ProviderError as error:
            delivery.error_code = error.code[:60]
            if error.retryable and delivery.attempts < settings.notification_max_attempts:
                delay = settings.notification_backoff_seconds * 2 ** (delivery.attempts - 1)
                delivery.status, delivery.next_attempt_at = (
                    DeliveryStatus.RETRYING,
                    now + timedelta(seconds=delay),
                )
                retrying += 1
            else:
                delivery.status, delivery.next_attempt_at = DeliveryStatus.FAILED, None
                failed += 1
                system_event_service.record(
                    session, category="notification", severity=EventSeverity.ERROR, source=f"notify:{provider.name}",
                    code=error.code, message="A notification could not be delivered.", shop_id=delivery.shop_id,
                )  # fmt: skip
        except Exception:  # noqa: BLE001  (a provider bug is a failed delivery, never a crash)
            delivery.status, delivery.error_code, delivery.next_attempt_at = (
                DeliveryStatus.FAILED,
                "provider_error",
                None,
            )
            failed += 1
            system_event_service.record(
                session, category="notification", severity=EventSeverity.ERROR, source=f"notify:{provider.name}",
                code="provider_error", message="A notification provider failed.", shop_id=delivery.shop_id,
            )  # fmt: skip
    session.flush()
    return DeliveryRun(attempted, sent, retrying, failed)


# --- The in-app inbox ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InboxItem:
    id: int
    event_type: str
    category: str
    title: str
    message: str
    created_at: datetime
    read_at: datetime | None
    entity_type: str | None
    entity_id: int | None


def _inbox_filter(ctx: RequestContext):  # noqa: ANN202
    return (
        NotificationDelivery.shop_id == ctx.shop_id,
        NotificationDelivery.user_id == ctx.user_id,
        NotificationDelivery.channel == NotificationChannel.IN_APP,
    )


def list_inbox(
    session: Session, ctx: RequestContext, *, unread_only: bool = False, limit: int = 20, offset: int = 0
) -> tuple[list[InboxItem], int]:
    conditions = list(_inbox_filter(ctx))
    if unread_only:
        conditions.append(NotificationDelivery.read_at.is_(None))
    total = session.scalar(select(func.count()).select_from(NotificationDelivery).where(*conditions)) or 0
    rows = session.execute(
        select(NotificationDelivery, NotificationEvent)
        .join(
            NotificationEvent,
            (NotificationEvent.shop_id == NotificationDelivery.shop_id)
            & (NotificationEvent.id == NotificationDelivery.event_id),
        )
        .where(*conditions)
        .order_by(NotificationEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        InboxItem(
            d.id,
            e.event_type,
            e.category,
            e.title,
            e.message,
            e.created_at,
            d.read_at,
            e.entity_type,
            e.entity_id,
        )
        for d, e in rows
    ]
    return items, total


def unread_count(session: Session, ctx: RequestContext) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(NotificationDelivery)
            .where(*_inbox_filter(ctx), NotificationDelivery.read_at.is_(None))
        )
        or 0
    )


def mark_read(session: Session, ctx: RequestContext, delivery_id: int) -> None:
    row = session.scalar(
        select(NotificationDelivery).where(*_inbox_filter(ctx), NotificationDelivery.id == delivery_id)
    )
    if row is None:
        raise NotFoundError("Notification not found")  # someone else's notification looks like this too
    if row.read_at is None:
        row.read_at = utc_now()
        session.flush()


def mark_all_read(session: Session, ctx: RequestContext) -> int:
    result = session.execute(
        update(NotificationDelivery)
        .where(*_inbox_filter(ctx), NotificationDelivery.read_at.is_(None))
        .values(read_at=utc_now())
    )
    return result.rowcount or 0


# --- Preferences -----------------------------------------------------------------------------------------------------------


def get_preferences(session: Session, ctx: RequestContext) -> dict[str, dict[str, bool]]:
    """Every category with the channels switched on for this person (defaults: in-app on, the rest off)."""
    saved = {
        p.category: p
        for p in session.scalars(
            select(NotificationPreference).where(
                NotificationPreference.shop_id == ctx.shop_id, NotificationPreference.user_id == ctx.user_id
            )
        )
    }
    return {
        category: {ch.value.lower(): _wants(saved.get(category), ch) for ch in NotificationChannel}
        for category in CATEGORIES
    }


def set_preference(
    session: Session, ctx: RequestContext, category: str, channels: Mapping[str, bool]
) -> None:
    if category not in CATEGORIES:
        raise InvalidInputError("Unknown notification category.", field="category")
    unknown = set(channels) - {c.value.lower() for c in NotificationChannel}
    if unknown:
        raise InvalidInputError(f"Unknown channel '{sorted(unknown)[0]}'.", field="channels")
    pref = _preference(session, ctx.shop_id, ctx.user_id, category)
    if pref is None:
        pref = NotificationPreference(
            shop_id=ctx.shop_id, user_id=ctx.user_id, category=category, in_app=True
        )
        session.add(pref)
    for name, value in channels.items():
        setattr(pref, name, bool(value))
    session.flush()
