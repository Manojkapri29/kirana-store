"""Exports of advanced reports (CSV, XLSX, PDF).

An export runs the very same report function the screen calls (`app.reporting.catalog`), so a figure in a file cannot differ from
the figure on screen. The caller needs ANALYTICS_EXPORT plus the permissions of the report itself. The file carries a title,
the shop, the period, the filters that were and were not applied, a generated time, the report's own notes, and (when
asked) chosen columns and one level of grouping with subtotals. A blank cell always means the value is unknown (Not Available),
never zero. CSV is UTF-8 with a BOM and neutralises spreadsheet formulas; XLSX has real number and date cells and fitted widths;
PDF has a repeated header row, the period and page numbers.
"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.reporting import builder, catalog
from app.reporting.filters import ReportFilters, ReportTable
from app.services import saved_report_service
from app.services.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.services.export_service import (
    CSV_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    Column,
    ExportFile,
    Kind,
    ReportFormat,
    render_pdf,
    render_report_csv,
    render_report_xlsx,
)
from app.services.shop_service import get_shop, shop_today

MAX_EXPORT_ROWS = 50_000
KINDS = {
    "text": Kind.TEXT,
    "integer": Kind.INTEGER,
    "money": Kind.MONEY,
    "quantity": Kind.QUANTITY,
    "percent": Kind.PERCENT,
    "date": Kind.DATE,
    "decimal": Kind.DECIMAL,
}
SUMMABLE = ("integer", "money", "quantity")


def available(ctx: RequestContext) -> list[dict]:
    """The reports this caller may export."""
    if not ctx.has("ANALYTICS_EXPORT"):
        return []
    return [
        {"key": s.key, "label": s.label}
        for s in catalog.REPORTS.values()
        if set(s.permissions) <= set(ctx.granted)
    ]


def _group(
    table: ReportTable, columns: list[tuple[str, str, str]], rows: list[dict], group_by: str
) -> tuple[list[dict], list[str]]:
    """Sort by `group_by` and add a subtotal row after each group and a grand total at the end."""
    kinds = {k: kind for k, _l, kind in columns}
    label_key = group_by
    known = [r for r in rows if r.get(group_by) is not None]
    unknown = [r for r in rows if r.get(group_by) is None]
    known.sort(key=lambda r: r[group_by])
    ordered = known + unknown

    def total_row(name: str, members: list[dict]) -> dict:
        out: dict = {label_key: name}
        for key, kind in kinds.items():
            if key == label_key or kind not in SUMMABLE:
                continue
            values = [m.get(key) for m in members]
            out[key] = (
                None
                if any(v is None for v in values)
                else sum((Decimal(v) for v in values), Decimal(0))
                if kind != "integer"
                else sum(values)
            )
        return out

    out: list[dict] = []
    current: list[dict] = []
    current_value: object = object()
    for r in ordered:
        if current and r.get(group_by) != current_value:
            out.append(total_row(f"Subtotal: {current_value}", current))
            current = []
        current_value = r.get(group_by)
        current.append(r)
        out.append(r)
    if current:
        out.append(total_row(f"Subtotal: {current_value}", current))
    out.append(total_row("Grand total", ordered))
    return out, [
        "Subtotals and the grand total add the rows shown. Percent, date and text columns are not added. A blank subtotal means a value in that group is unknown."
    ]


def _table_for(session: Session, ctx: RequestContext, key: str, filters: ReportFilters, today) -> ReportTable:  # noqa: ANN001
    """A catalog report, or a saved custom report (`saved-<id>`), run with the caller's own permissions."""
    if key.startswith("saved-"):
        if not ctx.has("ANALYTICS_CUSTOM_REPORT"):
            raise ForbiddenError("Your role cannot use custom reports.", code="permission_denied")
        try:
            report_id = int(key.removeprefix("saved-"))
        except ValueError:
            raise NotFoundError(f"Unknown report '{key}'.") from None
        saved = saved_report_service.get(session, ctx.shop_id, report_id)
        query = builder.validate(saved.definition, saved.dataset, set(ctx.granted))
        table = builder.run(session, ctx.shop_id, query, filters, today)
        table.title = saved.name
        return table
    return catalog.run(session, ctx.shop_id, key, filters, today, set(ctx.granted))


def export_report(
    session: Session, ctx: RequestContext, key: str, fmt: ReportFormat, filters: ReportFilters, *, columns: list[str] | None = None,
    group_by: str | None = None, title: str | None = None,
) -> ExportFile:  # fmt: skip
    if not ctx.has("ANALYTICS_EXPORT"):
        raise ForbiddenError(
            "Your role does not include the permission to export analytics.", code="permission_denied"
        )
    shop = get_shop(session, ctx.shop_id)
    today = shop_today(shop)
    from dataclasses import replace

    wide = replace(filters, limit=MAX_EXPORT_ROWS, offset=0)
    table = _table_for(session, ctx, key, wide, today)
    by_key = {k: (k, label, kind) for k, label, kind in table.columns}
    if columns:
        missing = [c for c in columns if c not in by_key]
        if missing:
            raise InvalidInputError(f"Unknown column(s): {', '.join(missing)}.", field="columns")
        if len(set(columns)) != len(columns):
            raise InvalidInputError("A column can be listed only once.", field="columns")
    chosen = [by_key[c] for c in columns] if columns else list(table.columns)
    rows = list(table.rows)
    notes = list(table.notes)
    if group_by:
        if group_by not in [c[0] for c in chosen]:
            raise InvalidInputError("Group by one of the columns that is exported.", field="group_by")
        rows, extra = _group(table, chosen, rows, group_by)
        notes += extra
    if table.total > len(table.rows):
        notes.append(f"Only the first {len(table.rows)} of {table.total} rows are included.")
    if any(r.get(k) is None for r in rows for k, _l, _kind in chosen):
        notes.append("A blank cell is Not Available: the value is unknown, not zero.")
    tz = ZoneInfo(shop.timezone)
    stamp = utc_now().astimezone(tz).strftime("%Y-%m-%d %H:%M ") + tz.key
    meta = [("Shop", shop.name)]
    if table.period:
        meta.append(
            (
                "Period",
                f"{table.period.label} ({table.period.start.isoformat()} to {table.period.end.isoformat()})",
            )
        )
    if table.comparison:
        meta.append(
            (
                "Compared with",
                f"{table.comparison.label} ({table.comparison.start.isoformat()} to {table.comparison.end.isoformat()})",
            )
        )
    if table.filters_applied:
        meta.append(("Filters applied", ", ".join(table.filters_applied)))
    if table.filters_ignored:
        meta.append(("Filters not applicable", ", ".join(table.filters_ignored)))
    meta.append(("Source", table.source or key))
    meta.append(("Generated", stamp))
    heading = (title or table.title or key).strip()[:120]
    cols = [Column(k, label, KINDS.get(kind, Kind.TEXT)) for k, label, kind in chosen]
    stem = f"{key}_{today.isoformat()}"
    if fmt is ReportFormat.CSV:
        return ExportFile(f"{stem}.csv", CSV_MEDIA_TYPE, render_report_csv(heading, meta, cols, rows, notes))
    if fmt is ReportFormat.XLSX:
        return ExportFile(
            f"{stem}.xlsx",
            XLSX_MEDIA_TYPE,
            render_report_xlsx(heading, meta, cols, rows, notes, sheet_name=key.replace("-", " ").title()),
        )
    return ExportFile(
        f"{stem}.pdf", PDF_MEDIA_TYPE, render_pdf(heading, meta, cols, rows, notes, f"Generated {stamp}")
    )
