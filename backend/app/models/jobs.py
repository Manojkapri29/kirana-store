"""Background jobs: work that should not hold up an HTTP request, recorded so it can be retried and inspected.

This is a database-backed queue with a provider-independent service in front of it (`background_job_service`). A job
has a type, a small JSON payload (never a secret, never a document), an idempotency key so the same work is not queued
twice, and a lifecycle: PENDING -> RUNNING -> COMPLETED, or FAILED / RETRYING (with a back-off) / CANCELLED. Only a safe
error code and message are kept.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import Base, IdType, TimestampMixin, enum_type, id_column, non_negative, not_blank
from app.models.enums import JobStatus


class BackgroundJob(TimestampMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("job_type", "idempotency_key"),
        Index("ix_background_jobs_due", "status", "run_after"),
        not_blank("job_type"),
        non_negative("attempts"),
    )

    id: Mapped[int] = id_column()
    job_type: Mapped[str] = mapped_column(String(60))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    shop_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("shops.id"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[JobStatus] = mapped_column(enum_type(JobStatus, "job_status"), default=JobStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    run_after: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_by: Mapped[str | None] = mapped_column(String(60))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(300))
