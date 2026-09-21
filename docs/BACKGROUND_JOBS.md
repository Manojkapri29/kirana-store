# Background jobs

Some work should not hold up a web request: retrying notifications, taking backups, cleaning old sessions, and (later) reading documents and images.
Phase 12 adds the **foundation**: a small provider-independent interface and a database-backed implementation. It does **not** move any existing
feature onto it, and it is not a heavy queue.

## What runs, and what does not

**Nothing runs in the background of the web server.** Enqueueing only records a job in `background_jobs`. Work happens when a **worker** runs:

```bash
python -m app.worker --once                 # run what is due, then exit (cron)
python -m app.worker --loop --schedule      # keep running; each pass also queues the routine jobs (idempotent)
python -m app.worker --list                 # recent jobs
```

If no worker runs, jobs wait as `PENDING` (visible to administrators at `/api/v1/admin/system/jobs`). Nothing pretends otherwise.

## The interface

`app/services/background_job_service.py`: `JobQueue` (`enqueue`, `run_due`, `cancel`, `get`) implemented by `DatabaseJobQueue`. A Celery, RQ,
Dramatiq or cloud-queue implementation would provide the same four methods; job **types** (`@register("name")` handlers) stay the same.

## A job

`id`, `job_type`, `idempotency_key`, optional `shop_id`, a small JSON `payload` (identifiers only: never a secret, token or document), `status`,
`attempts`/`max_attempts`, `run_after`, `created_at`, `started_at`, `completed_at`, `locked_by`, a JSON `result`, and a **safe** `error_code`/`error_message`.

**Statuses:** `PENDING` → `RUNNING` → `COMPLETED`; a failure becomes `RETRYING` with an exponentially growing delay
(`KIRANA_JOB_BACKOFF_SECONDS` × 2^(attempt−1)) until `max_attempts` (`KIRANA_JOB_MAX_ATTEMPTS`), then `FAILED` with a platform event; `CANCELLED` for a job
withdrawn before it started. A job stuck `RUNNING` for 15 minutes (its worker died) is picked up again.

## Guarantees and their limits

* **Queued once**: the same `(job_type, idempotency_key)` returns the existing job. The routine jobs use a date (or minute) key, so scheduling twice is harmless.
* **All or nothing**: a handler runs in its own transaction; on failure everything it wrote is rolled back.
* **At-least-once execution, not exactly-once**: if a worker dies after the handler committed but before the job was marked `COMPLETED`, the handler runs
  again. So **every handler must be idempotent**: repeating it must not duplicate a notification, a financial posting, an AI action or a backup's *effect*.
  Built-in handlers: `notifications.process_due` (a delivery is sent at most once), `maintenance.purge_sessions`, `maintenance.backup_retention` (a plan that never
  deletes the only valid backup), `backup.create` (a repeat makes one extra verified backup and never touches the last one).
* Errors never carry the exception text: an unexpected failure stores `handler_error` / "The job could not be completed."; a domain error stores its own safe message.
* Financial and inventory changes must **not** be put on this queue without an idempotency design of their own; they stay in the request that the person confirmed.

## Not built

Priorities, rate-limited queues, scheduled (cron-like) enqueueing beyond the routine jobs, a shop-facing job screen, and jobs for AI document processing and image
analysis (they still run inside their requests; the interface is ready for them).
