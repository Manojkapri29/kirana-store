# Backup and restore (SQLite)

## What a backup is

`python -m app.backup_cli create` (or `POST /api/v1/admin/backups`) copies the live database with SQLite's online backup API,
which gives a **consistent snapshot even while the application is writing** (copying the file with `cp` can produce a corrupt
copy). The copy is written under a temporary name, switched to a single self-contained file (no `-wal`/`-shm`), verified, and
only then given its real name:

```
backups/bkp_20260921T020000Z_a1b2c3.db     the database
backups/bkp_20260921T020000Z_a1b2c3.json   its manifest: id, time, size, SHA-256, schema revision, who started it
```

Verification checks SQLite's `integrity_check` and `foreign_key_check`, that an Alembic revision is present, and the SHA-256.
The record in `backup_records` (and the manifest) holds an id, created time, size, status, checksum, storage location name,
who started it and a safe error code. It holds **no connection string, credential or absolute path**.

If the disk is full or unwritable, or the copy fails verification, the backup is recorded as `FAILED`, a platform event and a
`BACKUP_FAILED` notification are raised (once a day), and nothing partial is left behind. Nothing is uploaded anywhere: the
only storage provider is `local` (`KIRANA_BACKUP_DIR`). An unknown provider name means "not configured", never a silent fall
back. The interface (`BackupStorage`) is where an S3-compatible provider would plug in; none is built.

## Retention

`KIRANA_BACKUP_KEEP_DAILY_DAYS` (default 14): every backup that recent is kept. Older backups are thinned to one per ISO week,
for `KIRANA_BACKUP_KEEP_WEEKLY_WEEKS` (default 8). The newest verified backup is always kept, and **the only valid backup is
never deleted**, however old. Failed backups do not count as valid ones. Deleting is deliberate: `retention` is a dry run
that prints the plan; `retention --apply` deletes the files, keeps the record (status `DELETED`) and logs it.

## Verify, rehearse, restore

```bash
python -m app.backup_cli list
python -m app.backup_cli verify  BACKUP_KEY        # checksum, integrity, schema, compatibility
python -m app.backup_cli rehearse BACKUP_KEY       # a trial restore on a temporary copy; the live database is untouched
```

`rehearse` copies the backup to a temporary folder, runs any pending migrations on the copy (an older backup is
`upgradable`), re-checks it and reports what it holds. Compatibility is `current`, `upgradable` (older: migrations will be
needed) or `incompatible` (unknown or newer revision: refused).

**Restoring replaces the live database.** Do it with the application **stopped**:

```bash
python -m app.backup_cli restore BACKUP_KEY --confirm "RESTORE BACKUP_KEY"
```

Safeguards, in order: the exact confirmation phrase; the backup must be `VERIFIED` and pass its checks again now; it must
not be incompatible; a fresh **pre-restore backup** of the current database is taken first (if that fails, nothing changes);
the copy is refused after a few seconds if any other connection holds the database ("in use" — nothing was changed);
the result is verified. Every attempt (validation, rehearsal, restore, refusal) is recorded with who, which backup, when, the
result and the failure reason, in `restore_records` and in an append-only `restore_log.jsonl` beside the backups (the log
survives a restore, which replaces the database that holds the table). If the backup was `upgradable`, run
`alembic upgrade head` before starting the application.

The API can restore only if `KIRANA_RESTORE_VIA_API_ENABLED=true` and the administrator is a SUPER_ADMIN with a valid
confirmation phrase. It is off by default: overwriting the production database from a web request is not recommended.

## PostgreSQL

These tools are for SQLite files. With PostgreSQL use `pg_dump`/`pg_basebackup` or your provider's snapshots; the application
reports backups as "not configured" for a non-SQLite database. See `POSTGRES_MIGRATION_CHECKLIST.md`.

## Not done

Off-machine copies and encryption of backups at rest are not built; encrypt the volume or copy backups with your own tool.
Restore drills are your responsibility: run `rehearse` regularly.


## Uploaded files (Phase 19 note)

The database backup does **not** contain uploaded product photos (local folder `KIRANA_IMAGE_STORAGE_DIR` or the S3 bucket). Back those up separately and restore them alongside the database; photos missing after a restore show as absent, the business data is unaffected. The restore drill test (`tests/test_phase19_restore_drill.py`) covers the database only.
