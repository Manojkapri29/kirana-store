"""Saved custom reports and the history of scheduled advanced-report runs.

A saved report stores a *definition* (dataset, fields, filters, grouping, aggregations) as JSON. The
definition is validated against the reporting allowlist when saved and again when run; it is never SQL and
never executed as text. Reports are archived, not deleted, so history and audit trails keep pointing at
something real.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import Base, IdType, TimestampMixin, id_column, not_blank, shop_id_column, tenant_fk


class SavedReport(TimestampMixin, Base):
    __tablename__ = "saved_reports"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "name"),
        tenant_fk("created_by", "users"),
        Index("ix_saved_reports_shop_archived", "shop_id", "is_archived"),
        not_blank("name"),
        not_blank("dataset"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    dataset: Mapped[str] = mapped_column(String(30))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[int] = mapped_column(IdType)
    updated_by: Mapped[int | None] = mapped_column(IdType)


class ReportRun(TimestampMixin, Base):
    """One run of a scheduled advanced report. `run_key` (schedule + period end) is unique, so a repeated
    run for the same period cannot create a second row: that is what makes scheduled runs idempotent."""

    __tablename__ = "report_runs"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "scheduled_report_id", "run_key"),
        tenant_fk("scheduled_report_id", "scheduled_reports"),
        Index("ix_report_runs_shop_schedule", "shop_id", "scheduled_report_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    scheduled_report_id: Mapped[int] = mapped_column(IdType)
    run_key: Mapped[str] = mapped_column(String(60))
    period_start: Mapped[str] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))  # OK | FAILED
    delivery_status: Mapped[str] = mapped_column(String(30))  # NOT_CONFIGURED | STORED_IN_APP
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)
