# Running in production

Phase 11 hardened the application for a real deployment. This page says what you must set, what the application
checks for you, and what it does **not** do. Anything not built is listed under "Not done".

## Environments

`KIRANA_ENVIRONMENT` is `development` (default), `test` or `production` (`testing`, `prod` and `dev` are accepted spellings).

In **production** the application refuses to start unless all of these hold. The refusal names the setting, never its value.

| Setting | Requirement |
| --- | --- |
| `KIRANA_SECRET_KEY` | set, at least 32 characters |
| `KIRANA_FRONTEND_URL` | an `https://` address |
| `KIRANA_CORS_ORIGINS` | not `*`, and includes the frontend URL |
| `KIRANA_DEBUG` | `false` |
| `KIRANA_LOG_LEVEL` | not `DEBUG` |
| `KIRANA_RATE_LIMIT_ENABLED` | `true` |

`backend/.env.example` lists every setting with placeholders only. `.env` is git-ignored. Never put a secret in a `VITE_*`
variable: the frontend bundle is public.

## Health checks

| Endpoint | Purpose | Detail |
| --- | --- | --- |
| `GET /health` | quick status | `ok` only |
| `GET /health/live` | process is up (no database touched) | `ok` only |
| `GET /health/ready` | database reachable, migrations at the code's head, configuration safe | check names and pass/fail only; HTTP 503 when not ready |

Public health output never contains versions, paths, hostnames or setting values. Detailed diagnostics
(`GET /api/v1/admin/system/health`) need an administrator token with the `system.health` permission.

## Before the first start

```bash
cd backend
.venv/bin/alembic upgrade head          # apply migrations (0014 is the Phase 11 migration)
.venv/bin/python -m app.integrity_cli   # read-only data check: exit 0 when nothing impossible is found
.venv/bin/python -m app.admin_cli create --email ops@example.com --name "Ops" --role SUPER_ADMIN   # prints a token once
```

Take a backup (`python -m app.backup_cli create`) **before** upgrading an existing database. The migration is additive and
tested for upgrade and downgrade, but a backup is the safety net.

## Scheduling (not built in)

The application has no scheduler. Use cron or your platform's scheduler:

```
0 2 * * *   cd /srv/kirana/backend && .venv/bin/python -m app.backup_cli create --scheduled
30 2 * * *  cd /srv/kirana/backend && .venv/bin/python -m app.backup_cli retention --apply
```

Notification retries are processed by `notification_service.process_due` when something calls it; no worker process ships with
the application, and no external channel ships with a provider (see `NOTIFICATIONS.md`).

## Rate limiting

An in-memory sliding window, per shop (or per client address where there is no shop). Groups: `ai`, `image`, `external`,
`coupon`, `export`, `admin`, `admin_auth_failures`, `auth`, `public`. Beyond the limit: HTTP 429, a safe message and `Retry-After`.
The counters live in one process: with several application processes each has its own window (a limitation, see below). Behind a
proxy, configure it to pass the real client address.

## Known limitations

* **Login exists since Phase 12** (`AUTHENTICATION.md`). There is no self-service password reset (no delivery channel): operators use
  `account_cli set-password`. See `PRODUCTION_DEPLOYMENT.md` for the deployment guide.
* Rate limit windows are per process (in memory).
* SQLite is one file and one writer. It is right for a single small deployment. See `POSTGRES_MIGRATION_CHECKLIST.md`.
* Backups are local files; there is no cloud storage provider. Copying them off the machine is your responsibility.
* No online store, online orders or public storefront exist, so no public API or online-order lifecycle needs hardening yet.
* Export and photo-analysis allowances count each accepted request, including one that later fails validation.

## Performance smoke test (Phase 11 findings)

`backend/tests/test_phase11_performance.py` seeds 3,000 products, 1,500 customers and 6,000 sales and asserts generous time
limits plus the query plans of the hot paths. On the development machine every screen answered well under a second (the
analytics overview over 6,000 sales: about 0.15 s; a 3,000-row export: under 0.1 s; the integrity check: under a second), and
each hot query used an existing index, so **no new index was added**. This is a smoke test, not a benchmark: it proves nothing
about concurrency or a large multi-shop database.
