"""Saved custom reports: shop-scoped, permission-controlled, editable, archived rather than deleted.

The stored definition is JSON that the reporting allowlist (`app.reporting.builder`) validates on every save
AND on every run, with the permissions of whoever is acting at that moment. So a report saved by an owner
cannot be used by a role that may not read its dataset, and a definition edited directly in the database
still cannot reach anything outside the allowlist.
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import SavedReport
from app.reporting import builder
from app.reporting.filters import ReportFilters, ReportTable
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError


def _clean(name: str, description: str | None) -> tuple[str, str | None]:
    name = (name or "").strip()
    if any(ord(c) < 32 for c in name + (description or "")):
        raise InvalidInputError("Names and descriptions cannot contain control characters.", field="name")
    if not name or len(name) > 80:
        raise InvalidInputError("Give the report a name of 1 to 80 characters.", field="name")
    description = (description or "").strip() or None
    if description and len(description) > 300:
        raise InvalidInputError("The description can be at most 300 characters.", field="description")
    return name, description


def _name_free(session: Session, shop_id: int, name: str, *, except_id: int | None = None) -> None:
    query = select(SavedReport.id).where(SavedReport.shop_id == shop_id, SavedReport.name == name)
    found = session.scalar(query)
    if found is not None and found != except_id:
        raise ConflictError("A saved report with this name already exists.", field="name")


def _get(session: Session, shop_id: int, report_id: int, *, lock: bool = False) -> SavedReport:
    query = select(SavedReport).where(SavedReport.id == report_id, SavedReport.shop_id == shop_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError("Saved report not found")
    return row


def get(session: Session, shop_id: int, report_id: int) -> SavedReport:
    return _get(session, shop_id, report_id)


def list_reports(session: Session, shop_id: int, *, include_archived: bool = False) -> list[SavedReport]:
    query = select(SavedReport).where(SavedReport.shop_id == shop_id).order_by(SavedReport.name)
    if not include_archived:
        query = query.where(SavedReport.is_archived.is_(False))
    return list(session.scalars(query))


def create(
    session: Session,
    ctx: RequestContext,
    *,
    name: str,
    description: str | None,
    dataset: str,
    definition: dict[str, Any],
) -> SavedReport:
    name, description = _clean(name, description)
    builder.validate(definition, dataset, set(ctx.granted))  # refuses anything outside the allowlist
    _name_free(session, ctx.shop_id, name)
    row = SavedReport(
        shop_id=ctx.shop_id,
        name=name,
        description=description,
        dataset=dataset,
        definition=definition,
        created_by=ctx.user_id,
    )
    session.add(row)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="saved_report",
        entity_id=row.id,
        action="saved_report_created",
        after={"name": name, "dataset": dataset},
    )
    return row


def update(
    session: Session,
    ctx: RequestContext,
    report_id: int,
    *,
    name: str,
    description: str | None,
    dataset: str,
    definition: dict[str, Any],
) -> SavedReport:
    row = _get(session, ctx.shop_id, report_id, lock=True)
    if row.is_archived:
        raise ConflictError("Restore an archived report before editing it.")
    name, description = _clean(name, description)
    builder.validate(definition, dataset, set(ctx.granted))
    _name_free(session, ctx.shop_id, name, except_id=row.id)
    before = {"name": row.name, "dataset": row.dataset, "definition": row.definition}
    row.name, row.description, row.dataset, row.definition, row.updated_by = (
        name,
        description,
        dataset,
        definition,
        ctx.user_id,
    )
    record_audit(
        session,
        ctx,
        entity_type="saved_report",
        entity_id=row.id,
        action="saved_report_updated",
        before=before,
        after={"name": name, "dataset": dataset, "definition": definition},
    )
    return row


def set_archived(session: Session, ctx: RequestContext, report_id: int, archived: bool) -> SavedReport:
    row = _get(session, ctx.shop_id, report_id, lock=True)
    row.is_archived, row.updated_by = archived, ctx.user_id
    record_audit(
        session,
        ctx,
        entity_type="saved_report",
        entity_id=row.id,
        action="saved_report_archived" if archived else "saved_report_restored",
    )
    return row


def run(
    session: Session, ctx: RequestContext, report_id: int, filters: ReportFilters, today: date
) -> ReportTable:
    row = _get(session, ctx.shop_id, report_id)
    if row.is_archived:
        raise ConflictError("This report is archived. Restore it to run it.")
    query = builder.validate(row.definition, row.dataset, set(ctx.granted))
    return builder.run(session, ctx.shop_id, query, filters, today)


def preview(
    session: Session,
    ctx: RequestContext,
    dataset: str,
    definition: dict[str, Any],
    filters: ReportFilters,
    today: date,
) -> ReportTable:
    query = builder.validate(definition, dataset, set(ctx.granted))
    return builder.run(session, ctx.shop_id, query, filters, today)
