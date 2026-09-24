# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| App refuses to start: "Unsafe production configuration: …" | The message names the setting (never the value). Fix it per [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md). |
| `/health/ready` returns 503 with `schema` failing | The database is behind the code. Back up, then `alembic upgrade head`. |
| Sign-in works but every request is 401 over plain HTTP | The production session cookie is `Secure`; browsers will not send it over HTTP. Use HTTPS (a proxy). Development uses a non-Secure cookie. |
| 403 with code `csrf` | The `X-CSRF-Token` header must equal the `kirana_csrf` cookie on every non-GET request. |
| `database is locked` | Too many writers on SQLite or the file is on a network share. Keep one machine; raise `KIRANA_DB_BUSY_TIMEOUT_MS`; see PERFORMANCE.md. |
| 429 Too Many Requests | A rate-limit group was exceeded (`Retry-After` is sent). Limits are in ENVIRONMENT_VARIABLES.md. |
| 403 `plan_limit` | The shop's plan lacks the feature. Operator: `python -m app.subscription_admin assign …`. |
| Integration says NOT_CONFIGURED | The environment variable *named* in the integration is unset. Set it, restart, press Test. Nothing is reported as working until a test succeeds. |
| Customer messages stay QUEUED / FAILED | See Integrations → Messages; the retry job runs in the worker (`python -m app.worker --loop --schedule`). Is the worker running? |
| Webhook returns 401/400 | Bad signature, timestamp older than 5 minutes, or unknown key. The body is never logged. |
| Offline sale shows CONFLICT | The stock or price changed before the phone reconnected. It is never auto-adjusted: open Offline → Conflicts and Retry or Discard. |
| Old screens after a release | The service worker updates in the background; the "New version available" banner reloads. `sw.js` is served `no-store`. |
| Photo missing after restore | Backups made with local photo storage include a `<key>.images.tar.gz`; restore brings the photos back. Photos in S3 are not in the backup: use the bucket's own versioning. |
| `Backups are not configured` / `dump_failed` on PostgreSQL | `pg_dump`/`pg_restore` must be installed and the database reachable; see BACKUP_AND_RESTORE.md. |
| An online order will not go to Delivered | The stock is no longer there (the message says which product). Restock or cancel the order; nothing was changed. |
| A customer cannot order | The store is closed, the product is not listed or is out of stock, the minimum order is not met, or they already have 5 orders waiting. |
| An error dialog shows `ERR-YYYYMMDD-XXXXX` | Search the server log for that reference; the cause is recorded there with secrets removed. |
