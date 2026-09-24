# Performance, scaling and PostgreSQL readiness (Phase 19)

All numbers were measured on SQLite (development database) on a developer laptop, through the real HTTP stack, against a
synthetic dataset of 25,000 sales, 3,000 products and 100,000 audit rows (`tests/test_phase19_baseline.py`, which prints
`PERF` lines). They are relative indicators, not a production benchmark. **PostgreSQL:** the same baseline was later run on a local PostgreSQL 16 server (table below). No load test was run against a remote database or a deployed environment.

## Baseline (SQLite)

| Endpoint | Time | Notes |
|---|---|---|
| Products / customers / inventory / sales pages | 3–16 ms | at most 6 queries, paginated |
| Dashboard overview | ~190 ms | |
| Inventory KPIs (year) | 1318 → **948 ms** | Phase 19 optimisation |
| Executive dashboard | 1219 → **897 ms** | Phase 19 optimisation |
| Finance dashboard | ~910 ms | 372 queries; not changed |
| Export sales (2.5 MB) | 417 ms | export does not block selling (tested) |
| Export products | 43 ms | |
| Analytics PDF | 31 ms | |
| Offline snapshot, products | 88 ms (517 KB) | |

## What changed in Phase 19

* `inventory_intelligence_service.inventory_health` computed the same stock/movement scan several times; it now computes it once
  and passes the rows to the fast/slow/dead-stock helpers (the helpers still work standalone).
* Argon2 hashing is memory hard (~64 MiB each). A bounded semaphore (`KIRANA_PASSWORD_HASH_CONCURRENCY`, default 4) stops a burst of
  sign-ins from exhausting memory; excess requests wait instead of failing.
* New rate-limit groups: `sync` (600/min), `message` (60/min), `payment` (120/min), in addition to the existing groups.
* Server-database pool settings (`KIRANA_DB_POOL_SIZE`, `_MAX_OVERFLOW`, `_POOL_RECYCLE_SECONDS`, `_POOL_TIMEOUT_SECONDS`), with
  `pool_pre_ping`. They only apply to non-SQLite URLs. The engine wiring is unit tested with a stub only.
* System health now reports work queues: pending/failed jobs, integrations in error, queued/failed messages, failed webhooks,
  payments needing review, open offline-sync conflicts.
* Finance trend granularity is bounded (refused beyond 62 steps; the builder buckets automatically).

## Decisions

* **No new index.** The Phase 16 EXPLAIN checks pass and the measured list queries use existing indexes.
* **No caching.** Nothing measured needed it, and a cache over money/stock figures adds staleness risk.
* **No list virtualisation.** Every list is server-paginated; the frontend is code-split (first download 1,121 kB → 418 kB).

## Concurrency (tested, SQLite)

Concurrent sign-ins, 10 concurrent sales with exact stock, 14 buyers competing for 10 units (10 succeed, 4 get 409), parallel Khata
payments, five devices replaying sync operations (replay is idempotent), an export running while sales continue.

## Scaling and PostgreSQL: what is and is not verified

Verified: all SQL is portable SQLAlchemy (no SQLite-only functions found); migrations render valid-looking PostgreSQL DDL
(`test_migration_renders_valid_looking_postgresql_ddl`); tenant isolation is enforced by `shop_id` in every query (a test scans the
SQL of every GET route).

Not verified: real execution on PostgreSQL, PostgreSQL connection-pool behaviour under load, multi-process deployment behind a load
balancer, background jobs running in more than one worker (run exactly one worker process for periodic jobs), and the S3 store
against a real provider. See `POSTGRES_MIGRATION_CHECKLIST.md` before moving off SQLite.

## Backup and restore

`tests/test_phase19_restore_drill.py` performs backup → further changes → restore and checks `integrity_check`,
`foreign_key_check`, the migration head, application integrity checks, and that the business continues. **Uploaded photos are not
part of the database backup**: back up the image directory (or the S3 bucket) separately; see `BACKUP_AND_RESTORE.md`.

## Rate limits

| Group | Default per minute |
|---|---|
| auth (sign-in etc.) | see `Settings.rate_limit_*` |
| sync | 600 |
| message | 60 |
| payment | 120 |
| webhook | see `rate_limit_webhook` |

## PostgreSQL 16 (local, one machine, same dataset, no ANALYZE after the bulk load)

| Endpoint | Time | Queries |
|---|---|---|
| Products / customers pages | 25–41 ms | 4 |
| Inventory page / sales page / quick sales page | 510 / 659 / 267 ms | 3 |
| Dashboard overview | 1.35 s | 37 |
| Inventory KPIs (year) / executive dashboard | 2.6 s / 1.9 s | 143 / 153 |
| Finance dashboard | 1.9 s | 370 |
| Export sales (2.5 MB) / products | 985 / 106 ms | 22 / 19 |
| Offline snapshot, products | 70 ms | 4 |

All within the test limits. The dashboards are slower than on SQLite mainly because the freshly loaded tables had no planner statistics (autovacuum analyses them in
normal running); the query counts are identical. Hot queries can use their indexes (`tests/test_phase11_performance.py`, PostgreSQL branch).
