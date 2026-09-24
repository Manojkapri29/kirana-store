# Production runbook

Everything here was exercised in the Phase 20 drill on a real process (production environment, fresh SQLite database, real CLIs, uvicorn on
localhost, plain HTTP, no Docker, no TLS). Steps marked *(unverified)* were not.

## Start

1. Set the environment (see ENVIRONMENT_VARIABLES.md). A missing/weak setting stops the start with a message naming it.
2. `alembic upgrade head` (or `KIRANA_RUN_MIGRATIONS=true` on the backend container).
3. `python -m app.account_cli create-shop …` for the first owner.
4. `uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --no-server-header` behind an HTTPS proxy *(the proxy: unverified)*.
5. `python -m app.worker --loop --schedule` as exactly **one** supervised process (periodic jobs: backups, scheduled reports, message retries, …).
6. Check `GET /health/live` (200) and `GET /health/ready` (database, schema, configuration all `ok`). `/docs` and `/metrics` are off (404) in production.

## Daily / weekly

* `python -m app.backup_cli create` (or the scheduled job), then `verify KEY`; **copy the backup folder and the photo folder/bucket off the machine**.
* `python -m app.integrity_cli` (read-only; exit 1 means an ERROR finding).
* The system health detail (admin console) shows work queues: failed jobs, integrations in error, queued/failed messages,
  failed webhooks, payments needing review, open offline-sync conflicts.

## Restore (application stopped)

```bash
python -m app.backup_cli list
python -m app.backup_cli verify KEY
python -m app.backup_cli rehearse KEY          # trial restore on a temp copy; live DB untouched
python -m app.backup_cli restore KEY --confirm "RESTORE KEY"
python -m app.integrity_cli && alembic current # 0021 (head)
# restore the photo folder / bucket from its own backup, then start the app
```

Drill result (Phase 20): after restore the data was exactly as of the backup (stock, khata balance), `integrity_check`, `foreign_key_check`
and the application integrity checks were clean, the schema was at head, sign-in worked and a new sale posted. **Two things to know:**

* The backup **does not contain uploaded photos**. Restoring the database alone leaves the product photo returning 404; copying the saved photo
  folder back restored it byte-for-byte (SHA-256 matched).
* The backup **registry lives inside the database**. Restoring an older backup makes newer backups disappear from `backup_cli list`. Their `.db` files
  remain in `KIRANA_BACKUP_DIR`; a restore automatically first makes a "pre-restore" backup file, so nothing is lost. To restore a file that is no longer listed,
  stop the app and replace the database file with it (delete `*.db-wal` / `*.db-shm` first), then run the integrity and `alembic current` checks above.

## Incident quick reference

| Situation | Action |
|---|---|
| Suspected data problem | `integrity_cli`; do not "fix" ledgers by hand. Posted history is never edited: use reversals / returns / adjustments. |
| Lost owner access | `account_cli set-password --email …`, `unlock --email …` |
| Compromised integration secret | Change the environment variable, restart, press Test. The database only holds the variable *name*. |
| Bad release | Stop, restore the pre-release backup, redeploy the previous image. Do not downgrade the schema. |

## Not verified

Docker images/compose, TLS/proxy configuration, PostgreSQL, S3 storage against a real provider, real SMTP/SMS/payment providers, load on real hardware,
multi-instance rate limiting (limits are per process), and monitoring/alerting (none is wired to any external service).
