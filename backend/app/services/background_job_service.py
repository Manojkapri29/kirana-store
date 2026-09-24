"""Background jobs: a provider-independent job interface, with a database-backed implementation.

The interface is small on purpose (`JobQueue`): enqueue work, run what is due, look at a job, cancel it. The implementation
here stores jobs in the `background_jobs` table and is run by a separate worker process (`python -m app.worker`) or by cron.
Another queue (Celery, RQ, Dramatiq, a cloud queue) would implement the same four methods; callers do not change.

What this is NOT: nothing runs in the background of the web server. Enqueueing a job only records it; work happens when a
worker runs. If no worker runs, jobs wait (and show as PENDING). That is stated in the admin screen and in the docs.

Rules for a job type (`register`):
  * the handler must be IDEMPOTENT: running it twice must not duplicate a notification, a backup record's effect, an AI action
    or a financial posting. The queue helps (a job is enqueued once per idempotency key, and retried only after a failure)
    but a crash between "work done" and "marked completed" replays the handler, so the handler itself must tolerate it;
  * the handler gets a session inside its own transaction and returns a small JSON-able result (no secrets, no documents);
  * the payload holds identifiers and settings, never a password, token, key or document.
Services never commit: the worker owns each transaction.
"""

import logging
import socket
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import metrics, observability
from app.core.config import Settings, get_settings
from app.db.session import read_session, write_transaction
from app.db.types import utc_now
from app.models import BackgroundJob
from app.models.enums import EventSeverity, JobStatus
from app.services import system_event_service
from app.services.errors import ConflictError, DomainError, InvalidInputError, NotFoundError

Handler = Callable[[Session, dict[str, Any]], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}
STUCK_AFTER = timedelta(minutes=15)  # a RUNNING job this old belongs to a worker that died: it is retried
OPEN = (JobStatus.PENDING, JobStatus.RETRYING)


class JobQueue(Protocol):
    def enqueue(
        self, session: Session, job_type: str, payload: dict[str, Any] | None = None, **options: Any
    ) -> BackgroundJob: ...
    def run_due(self, *, limit: int = 10, worker: str | None = None) -> list[dict[str, Any]]: ...
    def cancel(self, session: Session, job_id: int) -> BackgroundJob: ...
    def get(self, session: Session, job_id: int) -> BackgroundJob: ...


def register(job_type: str) -> Callable[[Handler], Handler]:
    def wrap(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return wrap


def worker_name() -> str:
    return f"{socket.gethostname()}"[:40]


def enqueue(
    session: Session,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
    shop_id: int | None = None,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
    settings: Settings | None = None,
) -> BackgroundJob:
    """Record a job. With an idempotency key, asking twice returns the same job (the work is queued once)."""
    settings = settings or get_settings()
    if job_type not in HANDLERS:
        raise InvalidInputError(f"Unknown job type: {job_type}", field="job_type")
    if idempotency_key is not None:
        existing = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == job_type, BackgroundJob.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing
    job = BackgroundJob(
        job_type=job_type, idempotency_key=idempotency_key, shop_id=shop_id, payload=payload or {},
        status=JobStatus.PENDING, attempts=0, max_attempts=max_attempts or settings.job_max_attempts,
        run_after=run_after or utc_now(),
    )  # fmt: skip
    session.add(job)
    session.flush()
    return job


def get(session: Session, job_id: int) -> BackgroundJob:
    job = session.get(BackgroundJob, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    return job


def list_jobs(session: Session, *, status: JobStatus | None = None, limit: int = 50) -> list[BackgroundJob]:
    query = select(BackgroundJob).order_by(BackgroundJob.id.desc()).limit(limit)
    if status is not None:
        query = query.where(BackgroundJob.status == status)
    return list(session.scalars(query))


def cancel(session: Session, job_id: int) -> BackgroundJob:
    job = session.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update())
    if job is None:
        raise NotFoundError("Job not found")
    if job.status not in OPEN:
        raise ConflictError("Only a job that has not started can be cancelled.")
    job.status = JobStatus.CANCELLED
    job.completed_at = utc_now()
    return job


def _claim(session: Session, limit: int, worker: str, now: datetime) -> list[int]:
    """Mark due jobs RUNNING (and revive jobs whose worker died). Returns their ids; each is then run on its own."""
    due = session.scalars(
        select(BackgroundJob)
        .where(
            ((BackgroundJob.status.in_(OPEN)) & (BackgroundJob.run_after <= now))
            | ((BackgroundJob.status == JobStatus.RUNNING) & (BackgroundJob.started_at <= now - STUCK_AFTER))
        )
        .order_by(BackgroundJob.run_after, BackgroundJob.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    ids = []
    for job in due:
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.started_at = now
        job.locked_by = worker
        ids.append(job.id)
    return ids


def _safe_error(error: BaseException) -> tuple[str, str]:
    """A code and a sentence that are safe to store and show: a domain error's own message, else a fixed one."""
    if isinstance(error, DomainError):
        return (error.code or type(error).__name__)[:60], error.message[:300]
    return "handler_error", "The job could not be completed."


def _run(job_id: int, settings: Settings) -> dict[str, Any]:
    with read_session() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        job_type, payload = job.job_type, dict(job.payload or {})
    handler = HANDLERS.get(job_type)
    outcome: dict[str, Any] = {"job_id": job_id, "type": job_type}
    error: BaseException | None = None
    result: dict[str, Any] | None = None
    if handler is None:
        error = InvalidInputError("No handler is registered for this job type.")
    else:
        try:
            with write_transaction() as session:  # the handler's work commits together, or not at all
                result = handler(session, payload)
        except Exception as exc:  # noqa: BLE001
            error = exc
            observability.log_event(
                "application",
                "background job failed",
                level=logging.WARNING,
                job_id=job_id,
                job_type=job_type,
                error=type(exc).__name__,
            )
    now = utc_now()
    with write_transaction() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        if job.status is not JobStatus.RUNNING:  # cancelled or finished by someone else meanwhile: leave it
            outcome["status"] = job.status.value
            return outcome
        if error is None:
            job.status, job.completed_at, job.result = JobStatus.COMPLETED, now, result
            job.error_code = job.error_message = None
        else:
            code, message = _safe_error(error)
            job.error_code, job.error_message = code, message
            if job.attempts < job.max_attempts:
                job.status = JobStatus.RETRYING
                job.run_after = now + timedelta(
                    seconds=settings.job_backoff_seconds * 2 ** (job.attempts - 1)
                )
            else:
                job.status, job.completed_at = JobStatus.FAILED, now
                system_event_service.record(
                    session, category="application", severity=EventSeverity.ERROR, source="jobs", code="job_failed",
                    message=f"Background job {job.job_type} (#{job.id}) failed after {job.attempts} attempts.",
                )  # fmt: skip
        job.locked_by = None
        outcome["status"] = job.status.value
        metrics.inc("kirana_job_runs_total", job_type=job.job_type, outcome=job.status.value)
    return outcome


def run_due(
    *, limit: int = 10, worker: str | None = None, settings: Settings | None = None
) -> list[dict[str, Any]]:
    """Run the jobs that are due, one after another, each in its own transaction. Returns what happened to each."""
    settings = settings or get_settings()
    with write_transaction() as session:
        ids = _claim(session, limit, worker or worker_name(), utc_now())
    return [_run(job_id, settings) for job_id in ids]


class DatabaseJobQueue:
    """The `JobQueue` implemented on the `background_jobs` table."""

    def enqueue(
        self, session: Session, job_type: str, payload: dict[str, Any] | None = None, **options: Any
    ) -> BackgroundJob:
        return enqueue(session, job_type, payload, **options)

    def run_due(self, *, limit: int = 10, worker: str | None = None) -> list[dict[str, Any]]:
        return run_due(limit=limit, worker=worker)

    def cancel(self, session: Session, job_id: int) -> BackgroundJob:
        return cancel(session, job_id)

    def get(self, session: Session, job_id: int) -> BackgroundJob:
        return get(session, job_id)


queue: JobQueue = DatabaseJobQueue()


# --- The job types that exist today ----------------------------------------------------------------------------------------------


@register("notifications.process_due")
def _notifications(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Send notification deliveries that are due (retries). Safe to repeat: a delivery is sent at most once."""
    from app.services import notification_service

    run = notification_service.process_due(session)
    return {k: v for k, v in vars(run).items() if isinstance(v, int)}


@register("backup.create")
def _backup(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Take a verified backup. A repeat only makes one more backup; it never damages the last one."""
    from app.models.enums import BackupKind
    from app.services import backup_service

    outcome = backup_service.perform_backup(BackupKind.SCHEDULED, "worker")
    backup_service.record_outcome(session, outcome)
    if not outcome.ok:
        raise InvalidInputError(outcome.error_message or "The backup failed.", code=outcome.error_code)
    return {"backup_key": outcome.key}


@register("reports.run_scheduled")
def _scheduled_reports(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Run every scheduled report that is due, across every shop. Safe to repeat: a report not yet due is skipped,
    and a repeat within the same shop-local day does not raise a second SCHEDULED_REPORT_READY notification
    (`notification_service.emit` de-duplicates by day)."""
    from app.services import scheduled_report_service

    return {"ran": scheduled_report_service.run_due(session)}


@register("maintenance.purge_sessions")
def _purge_sessions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services import auth_service

    return {"purged": auth_service.purge_expired(session)}


@register("maintenance.backup_retention")
def _retention(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services import backup_service

    plan = backup_service.apply_retention(session, actor="worker", dry_run=False)
    return {"deleted": len(plan["deleted"])}


def schedule_periodic(session: Session, now: datetime | None = None) -> list[BackgroundJob]:
    """Queue today's routine jobs, once each (the date is the idempotency key, so calling this again does nothing)."""
    day = (now or utc_now()).strftime("%Y%m%d")
    jobs = [
        enqueue(session, "backup.create", idempotency_key=f"daily:{day}"),
        enqueue(session, "maintenance.backup_retention", idempotency_key=f"daily:{day}"),
        enqueue(session, "maintenance.purge_sessions", idempotency_key=f"daily:{day}"),
        enqueue(
            session,
            "notifications.process_due",
            idempotency_key=f"tick:{(now or utc_now()).strftime('%Y%m%d%H%M')}",
        ),
    ]
    return jobs
