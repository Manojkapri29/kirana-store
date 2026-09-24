"""The shop owner's own operational picture: account state, plan usage, backup status, business health, notifications, audit.

Everything here is scoped to the signed-in shop. Nothing platform-wide appears (that is the administration API), and nothing
here changes business data. The account endpoints stay reachable when a shop is suspended or deactivated, so the owner can see why.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx, feature_flag
from app.core.config import get_settings
from app.db.session import get_session, write_transaction
from app.schemas.operations import (
    AccountOut,
    AlertOut,
    AuditEntryOut,
    AuditListOut,
    BackupStatusOut,
    HealthOut,
    NotificationListOut,
    NotificationOut,
    OverviewOut,
    PreferenceIn,
    PreferencesOut,
    UnreadOut,
    UsageItemOut,
    UsageOut,
)
from app.services import (
    account_service,
    alert_service,
    audit_service,
    entitlement_service,
    notification_service,
    operations_analytics_service,
    shop_service,
)

router = APIRouter(tags=["account"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get(
    "/account", response_model=AccountOut, summary="This shop's account state, plan and what it may use"
)
def account(ctx: Ctx, session: ReadSession) -> AccountOut:
    status, _reason = account_service.get_status(session, ctx.shop_id)
    e = entitlement_service.get_entitlements(session, ctx.shop_id)
    shop = shop_service.get_shop(session, ctx.shop_id)
    return AccountOut(
        shop_name=shop.name, account_status=status, message=account_service.owner_message(status),
        plan_code=e.plan_code, plan_name=e.plan_name, subscription_status=e.status,
        capabilities=entitlement_service.capabilities(session, ctx.shop_id),
    )  # fmt: skip


@router.get(
    "/account/usage", response_model=UsageOut, summary="Plan usage this month: used, limit, remaining"
)
def usage(ctx: Ctx, session: ReadSession) -> UsageOut:
    e = entitlement_service.get_entitlements(session, ctx.shop_id)
    report = entitlement_service.usage_report(session, ctx.shop_id)
    return UsageOut(
        period=entitlement_service.current_period(session, ctx.shop_id),
        plan_code=e.plan_code,
        limits={k: UsageItemOut(**v) for k, v in report.items()},  # type: ignore[arg-type]
    )


@router.get(
    "/account/backup-status", response_model=BackupStatusOut, summary="Was the platform backed up recently?"
)
def backup_status(ctx: Ctx, session: ReadSession) -> BackupStatusOut:
    return BackupStatusOut(**alert_service.backup_status(session))


@router.get(
    "/account/health",
    response_model=HealthOut,
    summary="Business health: alerts worked out from your records",
)
def health(ctx: Ctx, session: ReadSession) -> HealthOut:
    alerts = alert_service.business_health(session, ctx)
    return HealthOut(alerts=[AlertOut(kind=a.kind, severity=a.severity, title=a.title, message=a.message) for a in alerts],
                     backup=BackupStatusOut(**alert_service.backup_status(session)))  # fmt: skip


@router.get(
    "/analytics/overview",
    response_model=OverviewOut,
    summary="Sales, stock, customers, purchases, profit and offers in one view",
)
def analytics(
    ctx: Ctx,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
    bucket: Annotated[str, Query(pattern="^(day|week|month)$")] = "day",
) -> OverviewOut:
    """Built only from the existing reports and services (no second copy of the data). Sections that cannot be known say so."""
    return operations_analytics_service.overview(session, ctx, date_from, date_to, bucket)


@router.get("/audit-log", response_model=AuditListOut, summary="Who did what in this shop (owner only)")
def audit_log(
    ctx: Ctx,
    session: ReadSession,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AuditListOut:
    rows, total = audit_service.list_entries(
        session, ctx.shop_id, entity_type=entity_type, limit=limit, offset=offset
    )
    return AuditListOut(
        items=[AuditEntryOut(id=r.id, action=r.action, entity_type=r.entity_type, entity_id=r.entity_id, user_id=r.user_id,
                             request_id=r.request_id, created_at=r.created_at) for r in rows],
        total=total, limit=limit, offset=offset,
    )  # fmt: skip


# --- Notifications ---------------------------------------------------------------------------------------------------------

notifications = APIRouter(
    prefix="/notifications", tags=["notifications"], dependencies=[Depends(feature_flag("notifications"))]
)


@notifications.get("", response_model=NotificationListOut, summary="Your notifications, newest first")
def list_notifications(
    ctx: Ctx, session: ReadSession, unread_only: bool = False, limit: Limit = 20, offset: Offset = 0
) -> NotificationListOut:
    items, total = notification_service.list_inbox(
        session, ctx, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationListOut(
        items=[NotificationOut(id=i.id, event_type=i.event_type, category=i.category, title=i.title, message=i.message,
                               created_at=i.created_at, read=i.read_at is not None, entity_type=i.entity_type, entity_id=i.entity_id) for i in items],
        total=total, limit=limit, offset=offset, unread=notification_service.unread_count(session, ctx),
    )  # fmt: skip


@notifications.get("/unread-count", response_model=UnreadOut, summary="How many notifications are unread")
def unread(ctx: Ctx, session: ReadSession) -> UnreadOut:
    return UnreadOut(unread=notification_service.unread_count(session, ctx))


@notifications.post("/read-all", response_model=UnreadOut, summary="Mark every notification read")
def read_all(ctx: Ctx) -> UnreadOut:
    with write_transaction() as session:
        notification_service.mark_all_read(session, ctx)
        return UnreadOut(unread=notification_service.unread_count(session, ctx))


@notifications.post("/{delivery_id}/read", response_model=UnreadOut, summary="Mark one notification read")
def read_one(delivery_id: int, ctx: Ctx) -> UnreadOut:
    with write_transaction() as session:
        notification_service.mark_read(session, ctx, delivery_id)
        return UnreadOut(unread=notification_service.unread_count(session, ctx))


@notifications.post(
    "/refresh-alerts", response_model=UnreadOut, summary="Check the business now and add today's alerts"
)
def refresh_alerts(ctx: Ctx) -> UnreadOut:
    with write_transaction() as session:
        alert_service.refresh(session, ctx)
        return UnreadOut(unread=notification_service.unread_count(session, ctx))


@notifications.get(
    "/preferences", response_model=PreferencesOut, summary="Which notifications you want, per channel"
)
def get_preferences(ctx: Ctx, session: ReadSession) -> PreferencesOut:
    return PreferencesOut(
        preferences=notification_service.get_preferences(session, ctx),
        channels=notification_service.channel_status(get_settings(), session, ctx.shop_id),
    )


@notifications.put(
    "/preferences/{category}", response_model=PreferencesOut, summary="Change one category's channels"
)
def set_preference(category: str, payload: PreferenceIn, ctx: Ctx) -> PreferencesOut:
    with write_transaction() as session:
        notification_service.set_preference(session, ctx, category.upper(), payload.channels)
        return PreferencesOut(
            preferences=notification_service.get_preferences(session, ctx),
            channels=notification_service.channel_status(get_settings(), session, ctx.shop_id),
        )
