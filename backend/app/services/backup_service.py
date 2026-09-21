"""Backups: consistent copies of the database, verified, described, and retained by policy.

SQLite is copied with the database's own online-backup API, which takes a consistent snapshot even while other
connections are writing (copying the file with `cp` while writes happen can produce a corrupt copy). Each backup is:

  1. written to a temporary file next to its final name (never a half-written backup under a real name),
  2. verified: SQLite's integrity check, the migration revision, a SHA-256 checksum, and its size,
  3. renamed into place with a small manifest (`<key>.json`): the backup's own description, so a backup can be understood
     and checked even if the database that produced it is gone. The manifest holds no connection string or credential.

Nothing leaves the machine unless a storage provider that does so is configured. Only the `local` provider exists; the
interface (`BackupStorage`) is where an S3-compatible or cloud provider would plug in, and an unknown provider name is
reported as "not configured", never silently replaced by local storage.

This module does file work and returns descriptions; recording them in `backup_records`, notifying, and transactions are the
caller's job (an admin route or the CLI). Disk trouble (no space, no permission, a corrupt copy) is caught and returned as a
FAILED outcome with a safe code, not raised.
"""

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core import observability, schema_state
from app.core.config import BACKEND_DIR, Settings, get_settings
from app.db.types import utc_now
from app.models import BackupRecord
from app.models.enums import BackupKind, BackupStatus

MANIFEST_VERSION = 1


class BackupNotConfigured(Exception):
    """The storage provider named in the settings is not one that exists here."""


class BackupStorage(Protocol):
    name: str

    def directory(self) -> Path: ...
    def free_bytes(self) -> int: ...
    def path_of(self, filename: str) -> Path: ...
    def delete(self, filename: str) -> None: ...


class LocalBackupStorage:
    name = "local"

    def __init__(self, directory: str | Path) -> None:
        path = Path(directory)
        self._dir = path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    def directory(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.directory()).free

    def path_of(self, filename: str) -> Path:
        """A file inside the backup directory. A name that would leave it (a path, `..`) is refused."""
        if Path(filename).name != filename or filename.startswith("."):
            raise ValueError("unsafe backup file name")
        return self.directory() / filename

    def delete(self, filename: str) -> None:
        for name in (filename, filename.removesuffix(".db") + ".json"):
            path = self.path_of(name)
            if path.exists():
                path.unlink()


def get_storage(settings: Settings | None = None) -> BackupStorage:
    settings = settings or get_settings()
    if settings.backup_storage_provider == "local":
        return LocalBackupStorage(settings.backup_dir)
    raise BackupNotConfigured(settings.backup_storage_provider)


def database_file(settings: Settings | None = None) -> Path:
    """The SQLite file this application uses. Anything else (PostgreSQL) needs its own backup tool: see docs."""
    url = make_url((settings or get_settings()).database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BackupNotConfigured("database is not a SQLite file")
    path = Path(url.database)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


# --- Outcomes ------------------------------------------------------------------------------------------------


@dataclass
class BackupOutcome:
    key: str
    kind: BackupKind
    initiated_by: str
    ok: bool
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    schema_revision: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class Verification:
    ok: bool
    checks: dict[str, str]  # name -> "ok" or a short safe reason
    schema_revision: str | None = None
    compatibility: str | None = None  # current | upgradable | incompatible
    size_bytes: int | None = None
    sha256: str | None = None


def new_key(now: datetime | None = None) -> str:
    return f"bkp_{(now or utc_now()):%Y%m%dT%H%M%SZ}_{secrets.token_hex(3)}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inspect(path: Path) -> tuple[dict[str, str], str | None]:
    """Open a database file read-only and check it: integrity, foreign keys, migration revision."""
    checks: dict[str, str] = {}
    revision: str | None = None
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchall()
            checks["integrity"] = "ok" if result == [("ok",)] else "failed"
            checks["foreign_keys"] = (
                "ok" if not connection.execute("PRAGMA foreign_key_check").fetchall() else "failed"
            )
            try:
                row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                revision = row[0] if row else None
            except sqlite3.Error:
                revision = None
            checks["schema"] = "ok" if revision else "missing"
    except sqlite3.Error:
        checks["integrity"] = "unreadable"
    return checks, revision


def compatibility_of(revision: str | None) -> str:
    """Can this code use a database at `revision`? current = yes; upgradable = older, run migrations; else incompatible."""
    if revision is None or revision not in schema_state.known_revisions():
        return "incompatible"
    return "current" if revision == schema_state.code_head() else "upgradable"


def verify_file(path: Path, expected_sha256: str | None = None) -> Verification:
    """Verify a database file: exists, checksum (if one is known), integrity, migration revision and compatibility."""
    if not path.exists():
        return Verification(False, {"file": "missing"})
    checks: dict[str, str] = {"file": "ok"}
    sha = _sha256(path)
    if expected_sha256 is not None:
        checks["checksum"] = "ok" if sha == expected_sha256 else "mismatch"
    inspected, revision = _inspect(path)
    checks.update(inspected)
    compat = compatibility_of(revision)
    checks["compatibility"] = "ok" if compat != "incompatible" else "incompatible"
    ok = all(value == "ok" for value in checks.values())
    return Verification(ok, checks, revision, compat, path.stat().st_size, sha)


# --- Creating ------------------------------------------------------------------------------------------------


def _failure(key: str, kind: BackupKind, actor: str, code: str, message: str) -> BackupOutcome:
    return BackupOutcome(key, kind, actor, False, error_code=code, error_message=message)


def perform_backup(
    kind: BackupKind,
    initiated_by: str,
    settings: Settings | None = None,
    storage: BackupStorage | None = None,
) -> BackupOutcome:
    """Take, verify and store one backup. Never raises for a disk or database problem: returns a FAILED outcome."""
    settings = settings or get_settings()
    key = new_key()
    try:
        source = database_file(settings)
        store = storage or get_storage(settings)
    except BackupNotConfigured:
        return _failure(
            key,
            kind,
            initiated_by,
            "not_configured",
            "Backups are not configured for this database or storage.",
        )
    partial: Path | None = None
    try:
        size = source.stat().st_size
        needed = size + settings.backup_min_free_mb * 1024 * 1024
        if store.free_bytes() < needed:
            return _failure(
                key, kind, initiated_by, "disk_space", "There is not enough free disk space for a backup."
            )
        filename = f"{key}.db"
        final = store.path_of(filename)
        partial = store.path_of(f"{key}.partial")
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        try:
            target = sqlite3.connect(partial)
            try:
                source_connection.backup(target)  # a consistent snapshot, even while writes are happening
                target.execute(
                    "PRAGMA journal_mode = DELETE"
                )  # one self-contained file: no -wal/-shm left beside a backup
            finally:
                target.close()
        finally:
            source_connection.close()
        verification = verify_file(partial)
        if not verification.ok:
            partial.unlink(missing_ok=True)
            return _failure(
                key,
                kind,
                initiated_by,
                "verification_failed",
                "The backup could not be verified and was discarded.",
            )
        os.replace(partial, final)
        partial = None
        manifest: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "backup_key": key,
            "kind": kind.value,
            "created_at": utc_now().isoformat(),
            "filename": filename,
            "size_bytes": verification.size_bytes,
            "sha256": verification.sha256,
            "schema_revision": verification.schema_revision,
            "initiated_by": initiated_by,
        }
        store.path_of(f"{key}.json").write_text(json.dumps(manifest, indent=2))
        observability.log_event(
            "backup", "backup created", backup_key=key, size_bytes=verification.size_bytes
        )
        return BackupOutcome(
            key,
            kind,
            initiated_by,
            True,
            filename,
            verification.size_bytes,
            verification.sha256,
            verification.schema_revision,
        )
    except OSError:
        return _failure(key, kind, initiated_by, "disk_error", "The backup could not be written to disk.")
    except sqlite3.Error:
        return _failure(
            key, kind, initiated_by, "database_error", "The database could not be read for the backup."
        )
    except Exception:  # noqa: BLE001  (the reason goes to the log through the caller; the outcome stays safe)
        return _failure(key, kind, initiated_by, "unexpected", "The backup failed unexpectedly.")
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def record_outcome(session: Session, outcome: BackupOutcome, storage_name: str = "local") -> BackupRecord:
    """Save a backup's description in `backup_records` (and, if it failed, tell the operators)."""
    record = BackupRecord(
        backup_key=outcome.key,
        kind=outcome.kind,
        status=BackupStatus.VERIFIED if outcome.ok else BackupStatus.FAILED,
        storage_provider=storage_name,
        filename=outcome.filename,
        size_bytes=outcome.size_bytes,
        sha256=outcome.sha256,
        schema_revision=outcome.schema_revision,
        initiated_by=outcome.initiated_by,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        verified_at=outcome.created_at if outcome.ok else None,
    )
    session.add(record)
    session.flush()
    if not outcome.ok:
        from app.models.enums import EventSeverity
        from app.services import notification_service, system_event_service

        system_event_service.record(
            session, category="backup", severity=EventSeverity.ERROR, source="backup",
            code=outcome.error_code or "failed", message=outcome.error_message or "Backup failed.",
        )  # fmt: skip
        notification_service.emit_platform_event(
            session,
            "BACKUP_FAILED",
            "A backup could not be completed",
            "The automatic backup did not complete. Your administrator has been informed.",
            dedupe_suffix=f"{outcome.created_at:%Y%m%d}",
        )
    return record


def list_records(session: Session, *, limit: int = 50, offset: int = 0) -> list[BackupRecord]:
    return list(
        session.scalars(select(BackupRecord).order_by(BackupRecord.id.desc()).limit(limit).offset(offset))
    )


def get_record(session: Session, key: str) -> BackupRecord | None:
    return session.scalar(select(BackupRecord).where(BackupRecord.backup_key == key))


def verify_record(
    session: Session, record: BackupRecord, storage: BackupStorage | None = None
) -> Verification:
    """Re-verify a stored backup against the checksum recorded when it was made. Updates its status."""
    if record.filename is None or record.status is BackupStatus.DELETED:
        return Verification(False, {"file": "missing"})
    store = storage or get_storage()
    result = verify_file(store.path_of(record.filename), record.sha256)
    if result.ok:
        record.status = BackupStatus.VERIFIED
        record.verified_at = utc_now()
    elif record.status is BackupStatus.VERIFIED:
        record.status = BackupStatus.FAILED
        record.error_code, record.error_message = (
            "verification_failed",
            "A later check of this backup failed.",
        )
    session.flush()
    return result


# --- Retention -----------------------------------------------------------------------------------------------


def plan_retention(
    records: list[BackupRecord], now: datetime, settings: Settings | None = None
) -> tuple[list[BackupRecord], list[BackupRecord]]:
    """Which verified backups to keep and which may be deleted. Pure.

    Keep every backup from the last `backup_keep_daily_days` days; of older ones keep the newest of each calendar week for
    `backup_keep_weekly_weeks` weeks; the rest may be deleted. The newest verified backup is never deleted, and a policy
    that would leave no verified backup deletes nothing."""
    settings = settings or get_settings()
    live = sorted(
        (r for r in records if r.status is BackupStatus.VERIFIED), key=lambda r: r.created_at, reverse=True
    )
    if not live:
        return [], []
    daily_cutoff = now - timedelta(days=settings.backup_keep_daily_days)
    weekly_cutoff = (
        now
        - timedelta(weeks=settings.backup_keep_weekly_weeks)
        - timedelta(days=settings.backup_keep_daily_days)
    )
    keep: list[BackupRecord] = [live[0]]
    weeks_kept: set[tuple[int, int]] = set()
    for record in live[1:]:
        if record.created_at >= daily_cutoff:
            keep.append(record)
            continue
        iso = record.created_at.isocalendar()
        week = (iso[0], iso[1])
        if record.created_at >= weekly_cutoff and week not in weeks_kept:
            weeks_kept.add(week)
            keep.append(record)
    delete = [r for r in live if r not in keep]
    return keep, delete


def apply_retention(
    session: Session,
    *,
    actor: str,
    dry_run: bool = True,
    settings: Settings | None = None,
    storage: BackupStorage | None = None,
) -> dict[str, list[str]]:
    """Delete backups the policy no longer needs. Dry run by default: nothing is removed unless `dry_run=False`."""
    settings = settings or get_settings()
    now = utc_now()
    records = list(session.scalars(select(BackupRecord).where(BackupRecord.status == BackupStatus.VERIFIED)))
    keep, delete = plan_retention(records, now, settings)
    deleted: list[str] = []
    if not dry_run and delete:
        store = storage or get_storage(settings)
        for record in delete:
            if record.filename:
                try:
                    store.delete(record.filename)
                except (OSError, ValueError):
                    continue  # leave it recorded as verified: a failed delete is retried next time
            record.status = BackupStatus.DELETED
            record.deleted_at = now
            deleted.append(record.backup_key)
            observability.log_event(
                "backup", "backup deleted by retention", backup_key=record.backup_key, by=actor
            )
        session.flush()
    return {
        "keep": [r.backup_key for r in keep],
        "delete": [r.backup_key for r in delete],
        "deleted": deleted,
    }


def latest_verified(session: Session) -> BackupRecord | None:
    return session.scalar(
        select(BackupRecord)
        .where(BackupRecord.status == BackupStatus.VERIFIED)
        .order_by(BackupRecord.created_at.desc())
        .limit(1)
    )
