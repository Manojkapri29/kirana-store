# Migrations

Alembic, one linear chain, 21 revisions (`0001_initial_schema` … `0021_offline_sync`). `KIRANA_DATABASE_URL` selects the database
(alembic reads that variable, not `DATABASE_URL`).

## Verified in Phase 20 (SQLite)

* One head, no branches (`alembic heads`, `alembic branches`).
* Stepwise upgrade `0001 → 0021`, one revision at a time, on an empty database: every step succeeds.
* Stepwise downgrade `0021 → base`, one revision at a time: every step succeeds on an empty schema.
* Upgrade again, then `alembic check`: "No new upgrade operations detected" (models and migrations agree).
* `tests/test_migrations.py`, `test_phase9_migrations.py`, `test_phase10_migrations.py`, `test_phase11_migrations.py` test upgrades from earlier
  revisions with data, constraints, and PostgreSQL DDL rendering.

**Not verified:** running the migrations on a real PostgreSQL server. Downgrade with data is supported only where a migration says so
(`0015` refuses to downgrade while one email belongs to two shops). Production rollback is a **restore from backup**, not a downgrade.

## Rules

Take a backup first. Read the migration. Only the backend service migrates (`KIRANA_RUN_MIGRATIONS=true`); the worker never does.
New system-role permissions need both a `permissions.py` entry and a migration INSERT. `/health/ready` fails while the schema is behind the code.
