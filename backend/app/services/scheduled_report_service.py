"""Scheduled reports: ask for one of the existing summaries to be worked out again on a schedule, from the
same reporting services the screens use — nothing is calculated specially for a schedule. No email or SMS
is sent (no provider is bundled): each run stores a small JSON summary (figures and counts only, the same
shapes the API already returns; never a document, never raw customer or sales rows) and raises an in-app
`SCHEDULED_REPORT_READY` notification pointing at it. The full data stays one click away through the
existing export endpoints.

A run is idempotent: `run_due` is meant to be called by the worker on a timer, and calls this shop's
schedule again only once `next_run_at` has passed; running it twice close together does nothing extra
because `next_run_at` is always moved forward by the run that just happened, in the same transaction."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ScheduledReport
from app.models.enums import ReportSchedule
from app.services import (
    advanced_report_service,
    ai_insights_service,
    analytics_service,
    business_health_service,
    inventory_intelligence_service,
    khata_service,
    notification_service,
    sales_report_service,
)
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

REPORT_TYPES = (
    "sales_summary",
    "inventory_summary",
    "purchase_summary",
    "khata_summary",
    "business_health",
    "reorder",
)
INTERVAL_DAYS = {ReportSchedule.DAILY: 1, ReportSchedule.WEEKLY: 7, ReportSchedule.MONTHLY: 30}
ZERO = Decimal("0")


def _s(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def _run_report(session: Session, shop_id: int, report_type: str, today: date) -> dict[str, Any]:
    start = today - timedelta(days=29)
    if report_type == "sales_summary":
        s = sales_report_service.sales_summary(session, shop_id, start, today)
        return {
            "period": f"{start.isoformat()}/{today.isoformat()}",
            "net": _s(s.combined.net),
            "gross": _s(s.combined.gross),
            "transactions": s.combined.sales_count,
        }
    if report_type == "inventory_summary":
        h = inventory_intelligence_service.inventory_health(session, shop_id, today)
        return {
            "total_products": h.total_products,
            "low_stock": h.low_stock,
            "out_of_stock": h.out_of_stock,
            "stock_value": _s(h.total_stock_value),
        }
    if report_type == "purchase_summary":
        p = analytics_service.purchase_totals(session, shop_id, start, today)
        return {
            "period": f"{start.isoformat()}/{today.isoformat()}",
            "count": p.count,
            "total": _s(p.total),
            "net": _s(p.net),
        }
    if report_type == "khata_summary":
        accounts, owing = khata_service.list_accounts(
            session, shop_id, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
        )
        return {
            "customers_owing": owing,
            "outstanding_total": _s(sum((a.outstanding for a in accounts), ZERO)),
        }
    if report_type == "business_health":
        report = business_health_service.health_report(session, shop_id, today)
        return {
            "period": report.period_label,
            "anomalies": len(report.anomalies),
            "metrics": [
                {"label": m.label, "current": m.current, "change_percent": _s(m.change_percent)}
                for m in report.metrics
            ],
        }
    if report_type == "reorder":
        recs = ai_insights_service.reorder_recommendations(session, shop_id, today)
        return {"products_to_reorder": len(recs), "examples": [r.name for r in recs[:5]]}
    raise InvalidInputError(f"Unknown report type: {report_type}", field="report_type")


def create(
    session: Session, ctx: RequestContext, *, report_type: str, schedule: ReportSchedule
) -> ScheduledReport:
    if report_type not in REPORT_TYPES:
        raise InvalidInputError(f"Choose one of: {', '.join(REPORT_TYPES)}.", field="report_type")
    now = utc_now()
    row = ScheduledReport(
        shop_id=ctx.shop_id, report_type=report_type, schedule=schedule, created_by=ctx.user_id,
        next_run_at=now + timedelta(days=INTERVAL_DAYS[schedule]),
    )  # fmt: skip
    session.add(row)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="scheduled_report",
        entity_id=row.id,
        action="scheduled_report_created",
        after={"report_type": report_type, "schedule": schedule.value},
    )
    return row


def _get(session: Session, shop_id: int, report_id: int, *, lock: bool = False) -> ScheduledReport:
    query = select(ScheduledReport).where(ScheduledReport.id == report_id, ScheduledReport.shop_id == shop_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError("Scheduled report not found")
    return row


def get(session: Session, shop_id: int, report_id: int) -> ScheduledReport:
    return _get(session, shop_id, report_id)


def list_reports(session: Session, shop_id: int) -> list[ScheduledReport]:
    return list(
        session.scalars(
            select(ScheduledReport)
            .where(ScheduledReport.shop_id == shop_id)
            .order_by(ScheduledReport.id.desc())
        )
    )


def set_active(session: Session, ctx: RequestContext, report_id: int, active: bool) -> ScheduledReport:
    row = _get(session, ctx.shop_id, report_id, lock=True)
    row.is_active = active
    record_audit(
        session,
        ctx,
        entity_type="scheduled_report",
        entity_id=row.id,
        action="scheduled_report_reactivated" if active else "scheduled_report_deactivated",
    )
    return row


def update_schedule(
    session: Session, ctx: RequestContext, report_id: int, schedule: ReportSchedule
) -> ScheduledReport:
    row = _get(session, ctx.shop_id, report_id, lock=True)
    row.schedule = schedule
    row.next_run_at = utc_now() + timedelta(days=INTERVAL_DAYS[schedule])
    record_audit(
        session,
        ctx,
        entity_type="scheduled_report",
        entity_id=row.id,
        action="scheduled_report_updated",
        after={"schedule": schedule.value},
    )
    return row


def _run_one(session: Session, row: ScheduledReport) -> None:
    if advanced_report_service.is_advanced(row.report_type):
        advanced_report_service.run(session, row)  # idempotent per period; see that module
        row.last_run_at = utc_now()
        row.next_run_at = row.last_run_at + timedelta(days=INTERVAL_DAYS[row.schedule])
        return
    shop = get_shop(session, row.shop_id)
    today = shop_today(shop)
    try:
        result = _run_report(session, row.shop_id, row.report_type, today)
        row.last_result, row.last_status, row.last_error = result, "OK", None
        notification_service.emit_safely(
            session, row.shop_id, "SCHEDULED_REPORT_READY",
            title=f"{row.report_type.replace('_', ' ').title()} report is ready",
            message=(
                f"The scheduled {row.schedule.value.lower()} {row.report_type.replace('_', ' ')} "
                "report has been worked out."
            ),
            dedupe_key=f"scheduled_report:{row.id}:{today.isoformat()}",
            entity_type="scheduled_report", entity_id=row.id,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        row.last_status, row.last_error = "FAILED", str(exc)[:300]
    row.last_run_at = utc_now()
    row.next_run_at = row.last_run_at + timedelta(days=INTERVAL_DAYS[row.schedule])


def run_now(session: Session, ctx: RequestContext, report_id: int) -> ScheduledReport:
    row = _get(session, ctx.shop_id, report_id, lock=True)
    _run_one(session, row)
    record_audit(
        session,
        ctx,
        entity_type="scheduled_report",
        entity_id=row.id,
        action="scheduled_report_run",
        after={"status": row.last_status},
    )
    return row


def run_due(session: Session, *, now=None, limit: int = 50) -> int:
    """Run every active schedule whose time has come, across every shop (each shop only ever sees its own
    report). Called by the background worker; a schedule not yet due is untouched."""
    now = now or utc_now()
    due = session.scalars(
        select(ScheduledReport)
        .where(ScheduledReport.is_active.is_(True), ScheduledReport.next_run_at <= now)
        .limit(limit)
    ).all()
    for row in due:
        _run_one(session, row)
    return len(due)
