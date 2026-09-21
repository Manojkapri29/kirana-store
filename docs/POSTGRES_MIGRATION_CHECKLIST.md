# PostgreSQL migration checklist

**The application still runs on SQLite. No PostgreSQL migration was done in Phase 11.** This is the result of a read-only audit
of the code (searching for SQLite-specific constructs) and a list of what to do when the time comes. Nothing here has been run
against PostgreSQL.

## Already portable

* Models use SQLAlchemy types; money and quantity are `BigInteger` (integer paise / thousandths) through `TypeDecorator`s, so
  there is no floating point and no `Numeric` rounding difference.
* No `strftime`/`julianday`/`typeof`/`GROUP_CONCAT`/`INSERT OR`/`AUTOINCREMENT` in SQL. The `strftime` calls in the code are Python
  `datetime` formatting, not SQL.
* Text search uses `func.lower(...).contains(..., autoescape=True)`; PostgreSQL treats it the same (consider `ILIKE` and `pg_trgm`
  indexes for large catalogs).
* Timestamps are UTC through `UTCDateTime`.
* Insert-only triggers and CHECK constraints are created for both dialects in the migrations (`0001`, `0014` contain PostgreSQL
  branches).
* Row locking already uses `SELECT ... FOR UPDATE` (`with_for_update`, 19 places), which SQLite ignores and PostgreSQL honours.

## Must change or verify

| Item | Where | Action |
| --- | --- | --- |
| Writer transactions use `BEGIN IMMEDIATE` (execution option `sqlite_begin_immediate`) | `app/db/engine.py`, `app/db/session.py` | PostgreSQL uses normal `BEGIN`; concurrency then depends on `FOR UPDATE` locks and `SERIALIZABLE`/`READ COMMITTED` choices. Re-run `tests/test_concurrency.py` |
| SQLite pragmas (WAL, `foreign_keys`, `busy_timeout`) | `app/db/engine.py` | not needed; foreign keys are always enforced |
| Alembic `batch_alter_table` (73 uses) | migrations `0002`–`0014` | works on PostgreSQL but is unnecessary there; start PostgreSQL from a squashed baseline (`0001`…`0014` as one) or run the chain on an empty database |
| Partial unique indexes use `sqlite_where` | `models/purchasing.py`, `models/subscription.py` | add the matching `postgresql_where` |
| `server_default` values such as `expression.true()` | models | check literals (`1`/`0` vs `true`/`false`) |
| Document numbering counters | `numbering_service` | uses row locks: verify under concurrent posting |
| `sqlite_master`/`PRAGMA` use | `app/reporting/integrity.py`, `app/services/ai_planner.py` (a regex that *blocks* those words), `migrations/env.py` | the integrity checker runs `PRAGMA foreign_key_check` (check 12): guard it with a dialect test (PostgreSQL enforces foreign keys itself) |
| Backup and restore | `backup_service`, `restore_service`, `backup_cli` | SQLite only; replace with `pg_dump`/snapshots and document it |
| Error mapping matches SQLite wording ("locked", "busy") | `app/api/errors.py` (`OperationalError` handler) | add PostgreSQL messages (`deadlock detected`, `could not serialize`, `connection`) |
| `JSON` columns (audit detail, AI proposals, events) | models | fine; consider `JSONB` |
| Case and collation | name uniqueness and search | SQLite `lower()` vs PostgreSQL collations: confirm unique-name rules behave the same |
| Test fixtures copy a migrated SQLite file | `tests/conftest.py` | needs a PostgreSQL template database or schema-per-test |

## Suggested order

1. Add a `KIRANA_DATABASE_URL` for PostgreSQL in a scratch environment and run `alembic upgrade head` on an empty database.
2. Make the test fixtures dialect-aware and run the full suite; fix what fails (expect concurrency and error-message tests).
3. Load a copy of production data (export, then import row by row with sequences reset) and run `python -m app.integrity_cli`.
4. Rehearse cut-over with a maintenance window; keep the SQLite file as the rollback.
