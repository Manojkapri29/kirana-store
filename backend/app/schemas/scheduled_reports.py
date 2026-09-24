from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import ReportRun, ScheduledReport
from app.models.enums import ReportSchedule


class ScheduledReportCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: str
    schedule: ReportSchedule


class AdvancedScheduleIn(BaseModel):
    """Schedule an advanced or saved report. Filters are the allowlisted report filters, never SQL."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    schedule: ReportSchedule
    filters: dict[str, Any] = Field(default_factory=dict)
    saved_report_id: int | None = None
    export_format: str | None = None
    delivery_channel: str | None = None
    recipients: list[str] = Field(default_factory=list, max_length=10)


class ReportRunOut(BaseModel):
    id: int
    run_key: str
    period_start: str
    period_end: str
    status: str
    delivery_status: str
    error: str | None
    summary: dict[str, Any] | None
    generated_at: datetime

    @classmethod
    def of(cls, r: ReportRun) -> "ReportRunOut":
        return cls(
            id=r.id, run_key=r.run_key, period_start=r.period_start, period_end=r.period_end, status=r.status,
            delivery_status=r.delivery_status, error=r.error, summary=r.summary, generated_at=r.generated_at,
        )  # fmt: skip


class ScheduledReportUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ReportSchedule


class ScheduledReportOut(BaseModel):
    id: int
    report_type: str
    schedule: ReportSchedule
    is_active: bool
    created_by: int
    last_run_at: datetime | None
    next_run_at: datetime
    last_status: str | None
    last_error: str | None
    last_result: dict[str, Any] | None
    created_at: datetime
    params: dict[str, Any] | None = None
    export_format: str | None = None
    delivery_channel: str | None = None
    recipients: list[str] | None = None

    @classmethod
    def of(cls, r: ScheduledReport) -> "ScheduledReportOut":
        return cls(
            id=r.id, report_type=r.report_type, schedule=r.schedule, is_active=r.is_active,
            created_by=r.created_by, last_run_at=r.last_run_at, next_run_at=r.next_run_at,
            last_status=r.last_status, last_error=r.last_error,
            last_result=r.last_result, created_at=r.created_at, params=r.params,
            export_format=r.export_format,
            delivery_channel=r.delivery_channel, recipients=r.recipients,
        )  # fmt: skip


class ScheduledReportListOut(BaseModel):
    items: list[ScheduledReportOut]
