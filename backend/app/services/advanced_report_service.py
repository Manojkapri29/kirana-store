"""Scheduled ADVANCED reports (Phase 16), built on the existing scheduled-report architecture.

A schedule row is a `scheduled_reports` row whose `report_type` is one of `KINDS` (or `saved_report`). Nothing is calculated
specially for a schedule: each run calls the same reporting functions the screens and exports use, for the last COMPLETE period
of the schedule (yesterday; the previous Monday-to-Sunday week; the previous calendar month). A run stores a small JSON
summary (counts and totals, never raw rows and never customer names) in `report_runs` and raises an in-app notification.

Idempotent: a run is keyed by (schedule, period end). Running it again for the same period, from the worker or "run now",
returns the run that already exists and creates nothing. Delivery: no email provider is bundled, so a schedule that asks for
EMAIL gets `delivery_status = NOT_CONFIGURED` ("Delivery Channel Not Configured") and is never reported as sent.

A run acts with the permissions its creator has NOW: if they have lost access to the data, the run FAILS with that reason
rather than producing a report they may not read.
"""

import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ReportRun, SavedReport, ScheduledReport, User
from app.models.enums import MembershipStatus, ReportSchedule
from app.reporting import (
    builder,
    cohorts,
    customers,
    inventory,
    kpis,
    sales,
    suppliers,
)
from app.reporting import finance as finance_reports
from app.reporting.filters import CompareMode, Period, ReportFilters, ReportTable, build_filters
from app.services import authorization_service, messaging_service, notification_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

DELIVERY_NOT_CONFIGURED = "Delivery Channel Not Configured"
FORMATS = ("CSV", "XLSX", "PDF")
CHANNELS = ("EMAIL",)
FILTER_KEYS = {
    "product_id",
    "category_id",
    "brand",
    "supplier_id",
    "customer_id",
    "payment_method",
    "channel",
    "active",
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 10
SAVED = "saved_report"


def _flat(d: dict) -> dict:
    """The scalar figures of a report dict: money and counts as text, no names, no lists, no nested rows."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, int | str | bool) or v is None:
            out[k] = v
        elif isinstance(v, dict) and k in ("pnl", "previous"):
            out.update({f"{k}_{kk}": vv for kk, vv in _flat(v).items()})
    return {k: v for k, v in out.items() if k not in ("top_supplier",)}


def _table_summary(t: ReportTable) -> dict:
    totals: dict[str, str] = {}
    if t.total <= len(t.rows):  # the whole table is here, so a total is a true total
        for key, _label, kind in t.columns:
            values = [r.get(key) for r in t.rows]
            if kind == "money" and values and all(v is not None for v in values):
                totals[key] = str(sum((Decimal(v) for v in values), Decimal("0.00")))
    return {"title": t.title, "rows": t.total, "totals": totals}


def _kpi_summary(session: Session, shop_id: int, f: ReportFilters, today: date, granted: set[str]) -> dict:
    results, hidden = kpis.compute(session, shop_id, f, today, None, granted)
    return {
        "kpis": {
            r.definition.key: (
                str(r.current.amount) if r.current.amount is not None else r.current.availability.value
            )
            for r in results
        },
        "hidden": hidden,
    }


# kind -> (label, permissions ALL needed, runner)
Runner = Callable[[Session, int, ReportFilters, date, set[str]], dict]
KINDS: dict[str, tuple[str, tuple[str, ...], Runner]] = {
    "adv_kpis": ("KPI summary", ("ANALYTICS_VIEW",), lambda s, shop, f, t, g: _kpi_summary(s, shop, f, t, g)),
    "adv_executive": (
        "Executive summary",
        ("ANALYTICS_EXECUTIVE",),
        lambda s, shop, f, t, g: _kpi_summary(s, shop, f, t, g),
    ),
    "adv_sales": (
        "Sales summary",
        ("ANALYTICS_VIEW", "REPORT_VIEW"),
        lambda s, shop, f, t, g: _flat(sales.summary(s, shop, f)),
    ),
    "adv_inventory": (
        "Inventory by category",
        ("ANALYTICS_ADVANCED", "INVENTORY_VIEW"),
        lambda s, shop, f, t, g: _table_summary(inventory.category_summary(s, shop, _wide(f))),
    ),
    "adv_customers": (
        "Customer overview",
        ("ANALYTICS_ADVANCED", "CRM_ANALYTICS_VIEW"),
        lambda s, shop, f, t, g: _flat(customers.overview(s, shop, f, t)),
    ),
    "adv_cohorts": (
        "Cohort retention",
        ("ANALYTICS_ADVANCED", "CRM_ANALYTICS_VIEW"),
        lambda s, shop, f, t, g: _table_summary(cohorts.cohorts(s, shop, _wide(f), t)),
    ),
    "adv_suppliers": (
        "Supplier and purchase overview",
        ("ANALYTICS_ADVANCED", "SUPPLIER_VIEW", "PURCHASE_VIEW"),
        lambda s, shop, f, t, g: _flat(suppliers.overview(s, shop, f)),
    ),
    "adv_finance": (
        "Finance summary",
        ("ANALYTICS_ADVANCED", "FINANCE_VIEW"),
        lambda s, shop, f, t, g: _flat(finance_reports.summary(s, shop, f)),
    ),
}
ADVANCED_TYPES = (*KINDS, SAVED)


def is_advanced(report_type: str) -> bool:
    return report_type in ADVANCED_TYPES


def _wide(f: ReportFilters) -> ReportFilters:
    return ReportFilters(period=f.period, comparison=None, limit=200)


def period_for(schedule: ReportSchedule, today: date) -> Period:
    """The last COMPLETE day, week (Monday to Sunday) or calendar month before `today`."""
    if schedule is ReportSchedule.DAILY:
        d = today - timedelta(days=1)
        return Period(d, d, d.isoformat())
    if schedule is ReportSchedule.WEEKLY:
        end = today - timedelta(days=today.weekday() + 1)
        return Period(end - timedelta(days=6), end, f"Week ending {end.isoformat()}")
    end = today.replace(day=1) - timedelta(days=1)
    return Period(end.replace(day=1), end, end.strftime("%B %Y"))


def _filters(params: dict | None, period: Period, today: date) -> ReportFilters:
    extra = {k: v for k, v in ((params or {}).get("filters") or {}).items() if k in FILTER_KEYS}
    return build_filters(
        today,
        date_from=period.start,
        date_to=period.end,
        compare=CompareMode.PREVIOUS_PERIOD,
        limit=200,
        **extra,
    )


def _clean_params(kind: str, filters: dict | None, saved_report_id: int | None) -> dict:
    filters = filters or {}
    unknown = set(filters) - FILTER_KEYS
    if unknown:
        raise InvalidInputError(f"Unknown filter(s): {', '.join(sorted(unknown))}.", field="filters")
    params: dict[str, Any] = {"filters": filters}
    if kind == SAVED:
        if saved_report_id is None:
            raise InvalidInputError("Choose the saved report to schedule.", field="saved_report_id")
        params["saved_report_id"] = saved_report_id
    return params


def create(
    session: Session, ctx: RequestContext, *, kind: str, schedule: ReportSchedule, filters: dict | None = None,
    saved_report_id: int | None = None, export_format: str | None = None, delivery_channel: str | None = None,
    recipients: list[str] | None = None,
) -> ScheduledReport:  # fmt: skip
    if kind not in ADVANCED_TYPES:
        raise InvalidInputError(f"Choose one of: {', '.join(ADVANCED_TYPES)}.", field="kind")
    export_format = export_format.upper() if export_format else None
    if export_format is not None and export_format not in FORMATS:
        raise InvalidInputError(f"Format must be one of: {', '.join(FORMATS)}.", field="export_format")
    if delivery_channel is not None and delivery_channel.upper() not in CHANNELS:
        raise InvalidInputError(
            f"Delivery channel must be {' or '.join(CHANNELS)}, or left empty for in-app only.",
            field="delivery_channel",
        )
    delivery_channel = delivery_channel.upper() if delivery_channel else None
    recipients = [r.strip().lower() for r in (recipients or []) if r.strip()]
    if len(recipients) > MAX_RECIPIENTS or any(not EMAIL.match(r) for r in recipients):
        raise InvalidInputError(f"Give up to {MAX_RECIPIENTS} valid email addresses.", field="recipients")
    if delivery_channel and not recipients:
        raise InvalidInputError("Add at least one recipient for email delivery.", field="recipients")
    if recipients and not delivery_channel:
        raise InvalidInputError("Recipients need a delivery channel.", field="delivery_channel")
    _require(kind, set(ctx.granted), session, ctx.shop_id, saved_report_id)
    params = _clean_params(kind, filters, saved_report_id)
    _filters(
        params,
        period_for(schedule, shop_today(get_shop(session, ctx.shop_id))),
        shop_today(get_shop(session, ctx.shop_id)),
    )  # refuses bad filters now
    now = utc_now()
    row = ScheduledReport(
        shop_id=ctx.shop_id, report_type=kind, schedule=schedule, created_by=ctx.user_id, params=params, export_format=export_format,
        delivery_channel=delivery_channel, recipients=recipients or None, next_run_at=now + timedelta(days={ReportSchedule.DAILY: 1, ReportSchedule.WEEKLY: 7, ReportSchedule.MONTHLY: 30}[schedule]),
    )  # fmt: skip
    session.add(row)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="scheduled_report",
        entity_id=row.id,
        action="scheduled_report_created",
        after={
            "report_type": kind,
            "schedule": schedule.value,
            "delivery_channel": delivery_channel,
            "recipients": len(recipients),
        },
    )
    return row


def _require(
    kind: str, granted: set[str], session: Session, shop_id: int, saved_report_id: int | None
) -> None:
    if kind == SAVED:
        needed: tuple[str, ...] = ("ANALYTICS_CUSTOM_REPORT",)
    else:
        needed = KINDS[kind][1]
    missing = [p for p in needed if p not in granted]
    if missing:
        raise ForbiddenError(
            "Your role cannot schedule this report: it needs access to the data it shows.",
            code="permission_denied",
        )
    if kind == SAVED:
        saved = session.scalar(
            select(SavedReport).where(SavedReport.shop_id == shop_id, SavedReport.id == saved_report_id)
        )
        if saved is None:
            raise NotFoundError("Saved report not found")
        builder.validate(saved.definition, saved.dataset, granted)


def _creator_permissions(session: Session, row: ScheduledReport) -> set[str] | None:
    """What the schedule's creator may do today; None if they no longer belong to the shop."""
    user = session.scalar(select(User).where(User.shop_id == row.shop_id, User.id == row.created_by))
    if user is None or user.status is not MembershipStatus.ACTIVE or not user.is_active:
        return None
    if user.role_id is not None:
        from app.models import Role

        role = session.get(Role, user.role_id)
        if role is not None:
            return set(authorization_service.permissions_of_role(session, role))
    return set(RequestContext(shop_id=row.shop_id, user_id=user.id, role=user.role).granted)


def generate(session: Session, row: ScheduledReport, today: date) -> tuple[Period, dict]:
    """Work one advanced report out for the last complete period. Raises if the creator may no longer read the data."""
    granted = _creator_permissions(session, row)
    if granted is None:
        raise ForbiddenError("The person who scheduled this report no longer has access to the shop.")
    period = period_for(row.schedule, today)
    f = _filters(row.params, period, today)
    if row.report_type == SAVED:
        _require(SAVED, granted, session, row.shop_id, (row.params or {}).get("saved_report_id"))
        saved = session.scalar(
            select(SavedReport).where(
                SavedReport.shop_id == row.shop_id, SavedReport.id == (row.params or {})["saved_report_id"]
            )
        )
        if saved.is_archived:
            raise ConflictError("The saved report is archived, so it was not run.")
        table = builder.run(
            session, row.shop_id, builder.validate(saved.definition, saved.dataset, granted), _wide(f), today
        )
        return period, _table_summary(table)
    _label, needed, runner = KINDS[row.report_type]
    if not set(needed) <= granted:
        raise ForbiddenError(
            "The person who scheduled this report no longer has access to the data it shows."
        )
    return period, runner(session, row.shop_id, f, today, granted)


def run(session: Session, row: ScheduledReport) -> tuple[ReportRun, bool]:
    """Run once for the last complete period. Returns (the run, created). A second call for the same period creates nothing."""
    today = shop_today(get_shop(session, row.shop_id))
    period = period_for(row.schedule, today)
    key = f"{row.schedule.value}:{period.end.isoformat()}"
    existing = session.scalar(
        select(ReportRun).where(
            ReportRun.shop_id == row.shop_id,
            ReportRun.scheduled_report_id == row.id,
            ReportRun.run_key == key,
        )
    )
    if existing is not None and existing.status == "OK":
        return existing, False  # this period is done: repeating it creates nothing
    # A FAILED run of the same period is tried again into the SAME row (the person may have regained access, an archived report may
    # have been restored), so a transient failure does not block the period for good and there is still only one row per period.
    status, error, summary = "OK", None, None
    try:
        with session.begin_nested():
            period, summary = generate(session, row, today)
    except Exception as exc:  # noqa: BLE001
        status, error = "FAILED", str(exc)[:300]
    email = bool(row.delivery_channel)
    title = KINDS[row.report_type][0] if row.report_type in KINDS else "Saved report"
    emailed = "NOT_CONFIGURED"
    if email and status == "OK":
        # A real email goes out only if this shop has a working email integration; the body is the summary figures (counts and totals,
        # never a name or a raw row). Otherwise the run says so: "Delivery Channel Not Configured", never "sent".
        lines = [f"{k}: {v}" for k, v in (summary or {}).items() if not isinstance(v, dict | list)]
        emailed = messaging_service.send_report(
            session, row.shop_id, row.created_by, list(row.recipients or []), f"{title} - {period.label}",
            f"{title} for {period.label}\n\n" + "\n".join(lines) + "\n\nOpen the app for the full report.", f"report:{row.id}:{key}",
        )  # fmt: skip
    delivery_status = "NOT_DELIVERED" if status != "OK" else ({"NOT_CONFIGURED": "NOT_CONFIGURED", "SENT": "SENT"}.get(emailed, "FAILED") if email else "STORED_IN_APP")
    note = {"NOT_CONFIGURED": DELIVERY_NOT_CONFIGURED, "SENT": "Email sent", "FAILED": "Email could not be delivered"}.get(emailed) if email and status == "OK" else None
    fields = {
        "period_start": period.start.isoformat(), "period_end": period.end.isoformat(), "status": status,
        "delivery_status": delivery_status, "error": error,
        "summary": {**(summary or {}), **({"delivery": note} if note else {})} if summary is not None else None,
        "generated_at": utc_now(),
    }  # fmt: skip
    if existing is not None:
        run_row = existing
        for name, value in fields.items():
            setattr(run_row, name, value)
    else:
        run_row = ReportRun(shop_id=row.shop_id, scheduled_report_id=row.id, run_key=key, **fields)
        session.add(run_row)
    session.flush()
    row.last_run_at, row.last_status, row.last_error, row.last_result = (
        run_row.generated_at,
        status,
        error,
        run_row.summary,
    )
    if status == "OK":
        notification_service.emit_safely(
            session, row.shop_id, "SCHEDULED_REPORT_READY", title=f"{title} is ready",
            message=f"The {row.schedule.value.lower()} {title.lower()} for {period.label} is ready in the app."
            + (f" {note}." if note else ""),
            dedupe_key=f"advanced_report:{row.id}:{key}", entity_type="scheduled_report", entity_id=row.id,
        )  # fmt: skip
    return run_row, True


def runs_of(session: Session, shop_id: int, report_id: int, limit: int = 30) -> list[ReportRun]:
    if (
        session.scalar(
            select(ScheduledReport.id).where(
                ScheduledReport.shop_id == shop_id, ScheduledReport.id == report_id
            )
        )
        is None
    ):
        raise NotFoundError("Scheduled report not found")
    return list(
        session.scalars(
            select(ReportRun)
            .where(ReportRun.shop_id == shop_id, ReportRun.scheduled_report_id == report_id)
            .order_by(ReportRun.id.desc())
            .limit(limit)
        )
    )
