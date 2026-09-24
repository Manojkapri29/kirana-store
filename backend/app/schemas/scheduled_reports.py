from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import ScheduledReport
from app.models.enums import ReportSchedule


class ScheduledReportCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: str
    schedule: ReportSchedule


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

    @classmethod
    def of(cls, r: ScheduledReport) -> "ScheduledReportOut":
        return cls(
            id=r.id, report_type=r.report_type, schedule=r.schedule, is_active=r.is_active,
            created_by=r.created_by, last_run_at=r.last_run_at, next_run_at=r.next_run_at,
            last_status=r.last_status, last_error=r.last_error,
            last_result=r.last_result, created_at=r.created_at,
        )  # fmt: skip


class ScheduledReportListOut(BaseModel):
    items: list[ScheduledReportOut]
