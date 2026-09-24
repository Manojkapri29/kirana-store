"""Scheduled reports: a shop asks for a summary to be worked out on a schedule, from the same reporting
services the screens use. No email or SMS is sent (no provider is bundled): a run produces a small stored
result and an in-app notification pointing at it; the full data stays downloadable through the existing
export endpoints."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import ReportSchedule


class ScheduledReport(TimestampMixin, Base):
    __tablename__ = "scheduled_reports"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("created_by", "users"),
        Index("ix_scheduled_reports_shop_active", "shop_id", "is_active"),
        not_blank("report_type"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    report_type: Mapped[str] = mapped_column(
        String(40)
    )  # sales_summary | inventory_summary | purchase_summary | khata_summary | business_health | reorder
    schedule: Mapped[ReportSchedule] = mapped_column(enum_type(ReportSchedule, "report_schedule"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_by: Mapped[int] = mapped_column(IdType)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(20))  # OK | FAILED
    last_error: Mapped[str | None] = mapped_column(String(300))
    # A small JSON summary only (figures and counts, the same shape the API returns): never a document,
    # never raw rows.
    last_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
