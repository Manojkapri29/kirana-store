"""Business-health alerts for a shop owner, worked out from existing read-only services.

`business_health` computes the current picture live (nothing is stored); `refresh` turns it into in-app notifications, once per
day per kind (de-duplicated), so a shop owner is told without being nagged. Wording is neutral: it says what the numbers show
("Sales volume changed significantly compared with the selected baseline"), never why, and never accuses anyone.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.services import (
    ai_insights_service,
    backup_service,
    entitlement_service,
    inventory_service,
    khata_service,
    notification_service,
    system_event_service,
)
from app.services.shop_service import get_shop, shop_today

NEUTRAL_ANOMALY = {
    "sales_drop": "Sales volume changed significantly compared with the selected baseline (lower).",
    "sales_spike": "Sales volume changed significantly compared with the selected baseline (higher).",
    "discount_rate": "The share of sales given as discounts changed significantly compared with the usual level.",
    "returns": "The number of returns changed significantly compared with the usual level.",
    "stock_adjustment": "A large stock adjustment was recorded recently.",
    "quick_sales": "Quick Sale volume changed significantly compared with the usual level.",
}


@dataclass(frozen=True)
class Alert:
    kind: str  # low_stock, khata, plan_limit, unusual_activity, integration
    severity: str  # info or attention
    title: str
    message: str
    event_type: str
    dedupe: str  # what makes it the same alert today


def business_health(session: Session, ctx: RequestContext) -> list[Alert]:
    sid = ctx.shop_id
    today = shop_today(get_shop(session, sid))
    day = f"{today:%Y%m%d}"
    alerts: list[Alert] = []

    rows, _ = inventory_service.list_inventory(session, sid, active=True, limit=None)
    low = [r for r in rows if r.status is not inventory_service.StockStatus.IN_STOCK]
    if low:
        names = ", ".join(r.name for r in low[:3]) + (" and others" if len(low) > 3 else "")
        alerts.append(
            Alert(
                "low_stock",
                "attention",
                "Stock alert",
                f"{len(low)} product{'s are' if len(low) != 1 else ' is'} at or below the reorder level ({names}).",
                "LOW_STOCK",
                f"summary:{day}",
            )
        )

    accounts, _n = khata_service.list_accounts(
        session, sid, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
    )
    if accounts:
        alerts.append(
            Alert(
                "khata",
                "info",
                "Khata reminder",
                f"{len(accounts)} customer{'s have' if len(accounts) != 1 else ' has'} an outstanding balance.",
                "KHATA_REMINDER",
                f"summary:{day}",
            )
        )

    period = entitlement_service.current_period(session, sid)
    for key, entry in entitlement_service.usage_report(session, sid).items():
        pct = entry["percent_used"]
        if isinstance(pct, int) and pct >= 80:
            ai = key == "max_ai_requests_per_month"
            reached = pct >= 100
            label = entitlement_service.LIMIT_LABELS.get(key, key)
            alerts.append(
                Alert(
                    "plan_limit",
                    "attention",
                    "Plan usage",
                    f"Your plan's limit for {label} has been reached."
                    if reached
                    else f"You have used {pct}% of your plan's limit for {label}.",
                    "AI_USAGE_LIMIT" if ai else "SUBSCRIPTION_LIMIT",
                    f"{key}:{period}:{'full' if reached else 'near'}",
                )
            )

    for anomaly in ai_insights_service.anomalies(session, sid, today)[:3]:
        alerts.append(
            Alert(
                "unusual_activity",
                "info",
                "Worth a look",
                NEUTRAL_ANOMALY.get(anomaly.kind, "Something changed compared with the usual pattern."),
                "BUSINESS_ALERT",
                f"{anomaly.kind}:{day}",
            )
        )

    events = system_event_service.counts_since(session, 24, shop_id=sid)
    failures = sum(events.get("integration", {}).values())
    if failures >= 3:
        alerts.append(
            Alert(
                "integration",
                "info",
                "Outside services",
                "Price information was temporarily unavailable several times today. Billing is not affected.",
                "BUSINESS_ALERT",
                f"integration:{day}",
            )
        )
    return alerts


def refresh(session: Session, ctx: RequestContext) -> int:
    """Create in-app notifications for today's alerts (each kind once per day). Returns how many were created."""
    created = 0
    for alert in business_health(session, ctx):
        before = notification_service.emit_safely(
            session,
            ctx.shop_id,
            alert.event_type,
            title=alert.title,
            message=alert.message,
            dedupe_key=f"alert:{alert.kind}:{alert.dedupe}",
        )
        created += 1 if before is not None else 0
    return created


def backup_status(session: Session, settings: Settings | None = None) -> dict[str, str | None]:
    """For the shop owner: was the platform backed up recently? No paths, sizes or technical detail."""
    settings = settings or get_settings()
    try:
        backup_service.get_storage(settings)
        backup_service.database_file(settings)
    except backup_service.BackupNotConfigured:
        return {"state": "not_configured", "last_backup_at": None}
    latest = backup_service.latest_verified(session)
    if latest is None:
        return {"state": "none", "last_backup_at": None}
    age = utc_now() - latest.created_at
    stale = age > timedelta(hours=settings.backup_stale_after_hours)
    return {"state": "stale" if stale else "recent", "last_backup_at": latest.created_at.isoformat()}
