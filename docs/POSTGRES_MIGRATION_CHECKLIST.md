# PostgreSQL

PostgreSQL 16 was installed and used in the follow-up to Phase 20. What was **actually run** is listed here, with what is still not verified. SQLite
remains the default and the supported single-machine deployment; PostgreSQL is now a tested alternative for a larger or multi-instance deployment.

## Verified on a real PostgreSQL 16 server (localhost, one machine)

* **Migrations:** all 23 revisions upgrade on an empty database; `alembic check` reports no drift; downgrade to base and up again works.
* **Test suite:** the whole backend suite runs against PostgreSQL by setting `KIRANA_TEST_POSTGRES_URL` (see `tests/conftest.py`: one migrated template
  database, one copy per test). Result: everything passes except tests that are about SQLite itself and are skipped there (its file backup API,
  PRAGMAs, `EXPLAIN QUERY PLAN`, migration-data tests that build SQLite files). The skipped tests are listed by name in `conftest.py`.
* **Production-mode server on PostgreSQL:** the application started with production settings, passed `/health/ready`, and ran the whole business flow
  over HTTP (products, purchase, sale on credit, khata, an online order delivered into a sale); `python -m app.integrity_cli` was clean.
* **Backup and restore:** `python -m app.backup_cli create | verify | rehearse | restore` work on PostgreSQL through `pg_dump` / `pg_restore`
  (`app/services/pg_backup.py`, tested in `tests/test_pg_backup.py`): a verified custom-format dump, a trial restore into a scratch database, an
  all-or-nothing restore that brings data and id sequences back.
* **Performance smoke:** the Phase 19 baseline (25,000 sales, 3,000 products, 100,000 audit rows) passes on PostgreSQL with the same limits;
  indexes serve the hot queries. Numbers are in `PERFORMANCE.md`.

## Problems the real server found, now fixed

| Problem | Fix |
|---|---|
| A CHECK constraint compared a boolean with `1`/`0` (migration would not run) | `TRUE`/`FALSE` in the model and migration `0015` |
| `customers.referred_by_customer_id` was `Integer` in the model but `BigInteger` in the migration | model uses the shared id type |
| Seeded rows (plans, roles, ...) have explicit ids, so the id sequence lagged and the first new row collided | migration `0023` sets every sequence to its table's maximum (no-op on SQLite) |
| First use of a monthly usage counter raced under concurrency (duplicate key) | insert in a savepoint, then read and lock the winner's row |
| A NUL character in any text made PostgreSQL refuse the value, giving HTTP 500 | database "bad data" errors now answer 422 with a plain message |
| Deadlock / serialisation failures were treated as unexpected 500s | now a retryable 503 |
| Backups said "not configured" | `pg_dump`-based backup and restore |

## After a bulk load, run ANALYZE

A report over 20,000 freshly bulk-loaded sales took **24 seconds** on PostgreSQL until planner statistics existed, and **20 ms** after `ANALYZE`. Normal running is fine
(autovacuum analyses tables), but after importing or restoring a large amount of data run `ANALYZE;` (a restore from `pg_restore` does not always leave statistics).

## Requirements to run on PostgreSQL

* `pip install -r requirements.txt` plus a driver: `pip install "psycopg[binary]"` (kept out of the base requirements because SQLite is the default).
* `KIRANA_DATABASE_URL=postgresql+psycopg://user:password@host:5432/db` (from the environment; never in Git). Optional pool settings
  `KIRANA_DB_POOL_SIZE`, `_MAX_OVERFLOW`, `_POOL_RECYCLE_SECONDS`, `_POOL_TIMEOUT_SECONDS` (see `ENVIRONMENT_VARIABLES.md`).
* `pg_dump` and `pg_restore` (PostgreSQL client tools) on the machine that takes backups, with the same major version as the server or newer.
* A role that owns the application's schema; the rehearsal command needs `CREATEDB`. Require SSL (`sslmode=verify-full`) for a remote server.
* Run migrations from one place as a release step (`alembic upgrade head`). Run exactly one worker process for periodic jobs.
* Rate limiting and metrics are still per process: several instances need a shared store (Redis).

## Not verified

* A **remote** managed PostgreSQL service, SSL/TLS to it, PgBouncer, replicas, and behaviour under real multi-instance load.
* PostgreSQL versions other than 16.
* Moving an existing SQLite database to PostgreSQL: there is no import tool. Start a new PostgreSQL database, or export/import by your own means and then run
  `python -m app.integrity_cli`. Keep the SQLite file as the rollback.
* Text search uses `lower(...) LIKE '%term%'`: fine at shop scale on PostgreSQL, but a large catalogue would want a `pg_trgm` index.
