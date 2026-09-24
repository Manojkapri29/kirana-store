"""Scheduled reports. See `scheduled_report_service`: no email is sent; a run stores a small summary and
raises an in-app notification pointing at it."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.scheduled_reports import (
    ScheduledReportCreateIn,
    ScheduledReportListOut,
    ScheduledReportOut,
    ScheduledReportUpdateIn,
)
from app.services import scheduled_report_service

router = APIRouter(prefix="/scheduled-reports", tags=["scheduled reports"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=ScheduledReportListOut)
def list_reports(ctx: Ctx, session: ReadSession) -> ScheduledReportListOut:
    return ScheduledReportListOut(
        items=[ScheduledReportOut.of(r) for r in scheduled_report_service.list_reports(session, ctx.shop_id)]
    )


@router.post("", response_model=ScheduledReportOut, status_code=201)
def create_report(payload: ScheduledReportCreateIn, ctx: Ctx) -> ScheduledReportOut:
    with write_transaction() as session:
        return ScheduledReportOut.of(
            scheduled_report_service.create(
                session, ctx, report_type=payload.report_type, schedule=payload.schedule
            )
        )


@router.get("/{report_id}", response_model=ScheduledReportOut)
def get_report(report_id: int, ctx: Ctx, session: ReadSession) -> ScheduledReportOut:
    return ScheduledReportOut.of(scheduled_report_service.get(session, ctx.shop_id, report_id))


@router.patch("/{report_id}", response_model=ScheduledReportOut)
def update_report(report_id: int, payload: ScheduledReportUpdateIn, ctx: Ctx) -> ScheduledReportOut:
    with write_transaction() as session:
        return ScheduledReportOut.of(
            scheduled_report_service.update_schedule(session, ctx, report_id, payload.schedule)
        )


@router.post("/{report_id}/deactivate", response_model=ScheduledReportOut)
def deactivate(report_id: int, ctx: Ctx) -> ScheduledReportOut:
    with write_transaction() as session:
        return ScheduledReportOut.of(scheduled_report_service.set_active(session, ctx, report_id, False))


@router.post("/{report_id}/reactivate", response_model=ScheduledReportOut)
def reactivate(report_id: int, ctx: Ctx) -> ScheduledReportOut:
    with write_transaction() as session:
        return ScheduledReportOut.of(scheduled_report_service.set_active(session, ctx, report_id, True))


@router.post("/{report_id}/run-now", response_model=ScheduledReportOut)
def run_now(report_id: int, ctx: Ctx) -> ScheduledReportOut:
    with write_transaction() as session:
        return ScheduledReportOut.of(scheduled_report_service.run_now(session, ctx, report_id))
