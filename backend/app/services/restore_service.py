"""Restoring a backup, safely. Three levels, each stricter than the last.

  validate    Is the backup file intact? Checksum against its manifest, SQLite integrity, migration revision, and can this
              version of the code use it (current, upgradable by migrations, or incompatible).
  rehearse    Do everything a restore would, on a COPY in a temporary folder: verify, run any pending migrations on the copy,
              re-check integrity, and report what it holds. The live database is untouched.
  restore     Replace the live database with the backup. Only with an explicit confirmation phrase naming the backup, only for
              a backup that validates and is not incompatible, and only after a fresh "pre-restore" backup of the current
              database has been taken and verified (if that fails, nothing is changed). It copies with SQLite's backup API into
              the live file: if the database is in use and cannot be taken exclusively, the copy fails and nothing changes.

Every attempt (validate, rehearsal, restore, refusal) is recorded with who, which backup, when, the result and the failure
reason, in `restore_records` and in an append-only `restore_log.jsonl` next to the backups (the log survives a restore, which
replaces the database that holds the table).

The API only exposes a restore if `KIRANA_RESTORE_VIA_API_ENABLED=true`; the command line (`python -m app.backup_cli`) always can,
because it runs with the application stopped, which is the safe way to restore.
"""

import json
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core import observability
from app.core.config import BACKEND_DIR, Settings, get_settings
from app.db.types import utc_now
from app.models import BackupRecord
from app.models.enums import BackupKind, BackupStatus, RestoreMode
from app.services import backup_service
from app.services.backup_service import BackupStorage, Verification

CONFIRM_PREFIX = "RESTORE "


@dataclass
class RestoreResult:
    ok: bool
    mode: RestoreMode
    backup_key: str
    detail: str
    verification: Verification | None = None
    pre_restore_key: str | None = None
    report: dict[str, int] | None = None


def confirmation_phrase(backup_key: str) -> str:
    return f"{CONFIRM_PREFIX}{backup_key}"


def _log(storage: BackupStorage, result: RestoreResult, actor: str) -> None:
    """Append-only line in the restore log next to the backups. Never raises."""
    entry = {
        "at": utc_now().isoformat(), "mode": result.mode.value, "backup_key": result.backup_key,
        "initiated_by": actor, "ok": result.ok, "detail": result.detail, "pre_restore_key": result.pre_restore_key,
    }  # fmt: skip
    try:
        with storage.path_of("restore_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except (OSError, ValueError):
        pass
    observability.log_event(
        "backup",
        f"restore {result.mode.value.lower()} {'ok' if result.ok else 'failed'}",
        backup_key=result.backup_key,
        by=actor,
    )


def validate(record: BackupRecord, actor: str, storage: BackupStorage | None = None) -> RestoreResult:
    store = storage or backup_service.get_storage()
    if record.filename is None or record.status is BackupStatus.DELETED:
        result = RestoreResult(
            False, RestoreMode.VALIDATE, record.backup_key, "This backup has no usable file."
        )
    else:
        verification = backup_service.verify_file(store.path_of(record.filename), record.sha256)
        result = RestoreResult(
            verification.ok, RestoreMode.VALIDATE, record.backup_key,
            "The backup is intact and usable." if verification.ok else "The backup did not pass its checks.", verification,
        )  # fmt: skip
    _log(store, result, actor)
    return result


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def rehearse(record: BackupRecord, actor: str, storage: BackupStorage | None = None) -> RestoreResult:
    """A dry run of a restore on a temporary copy. The live database is never opened for writing."""
    store = storage or backup_service.get_storage()
    if record.filename is None:
        result = RestoreResult(
            False, RestoreMode.REHEARSAL, record.backup_key, "This backup has no usable file."
        )
        _log(store, result, actor)
        return result
    with tempfile.TemporaryDirectory(prefix="rehearsal_") as folder:
        copy = Path(folder) / "rehearsal.db"
        try:
            shutil.copyfile(store.path_of(record.filename), copy)
        except OSError:
            result = RestoreResult(
                False, RestoreMode.REHEARSAL, record.backup_key, "The backup file could not be read."
            )
            _log(store, result, actor)
            return result
        first = backup_service.verify_file(
            copy, record.sha256
        )  # the copy is byte-identical, so the checksum must match
        if not first.ok:
            result = RestoreResult(
                False, RestoreMode.REHEARSAL, record.backup_key, "The backup did not pass its checks.", first
            )
            _log(store, result, actor)
            return result
        detail = "The backup restores cleanly."
        try:
            if first.compatibility == "upgradable":
                command.upgrade(
                    _alembic_config(f"sqlite:///{copy}"), "head"
                )  # migrate the COPY, never the live database
                detail = "The backup restores cleanly and would need the newer migrations, which applied without problems."
            final = backup_service.verify_file(copy)
            report: dict[str, int] = {}
            with sqlite3.connect(f"file:{copy}?mode=ro", uri=True) as connection:
                for table in ("shops", "products", "sales", "purchases", "customers"):
                    try:
                        report[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608  (fixed names)
                    except sqlite3.Error:
                        continue
            ok = final.ok and final.compatibility == "current"
        except Exception:  # noqa: BLE001
            ok, final, report, detail = (
                False,
                first,
                {},
                "A rehearsal on a copy failed, so this backup should not be restored.",
            )
        result = RestoreResult(ok, RestoreMode.REHEARSAL, record.backup_key, detail, final, report=report)
    _log(store, result, actor)
    return result


IN_USE_WAIT_SECONDS = 5.0


def restore(
    record: BackupRecord,
    *,
    confirmation: str,
    actor: str,
    via_api: bool,
    settings: Settings | None = None,
    storage: BackupStorage | None = None,
    pre_restore_recorder=None,  # noqa: ANN001  (called with the pre-restore BackupOutcome so the caller can store it)
) -> RestoreResult:
    """Replace the live database with this backup. See the module docstring for every safeguard."""
    settings = settings or get_settings()
    store = storage or backup_service.get_storage(settings)

    def refuse(reason: str) -> RestoreResult:
        result = RestoreResult(False, RestoreMode.RESTORE, record.backup_key, reason)
        _log(store, result, actor)
        return result

    if via_api and not settings.restore_via_api_enabled:
        return refuse(
            "Restoring from the web interface is turned off. Use the command line with the application stopped."
        )
    if confirmation != confirmation_phrase(record.backup_key):
        return refuse("The confirmation did not match. Type the exact phrase shown to confirm the restore.")
    if record.filename is None or record.status is not BackupStatus.VERIFIED:
        return refuse("Only a verified backup can be restored.")
    verification = backup_service.verify_file(store.path_of(record.filename), record.sha256)
    if not verification.ok or verification.compatibility == "incompatible":
        return refuse("The backup did not pass its checks or is not compatible with this version.")

    # Safety copy of what is there now. If it cannot be made, nothing is touched.
    safety = backup_service.perform_backup(BackupKind.PRE_RESTORE, actor, settings, store)
    if pre_restore_recorder is not None:
        pre_restore_recorder(safety)
    if not safety.ok:
        return refuse("A safety backup of the current database could not be made, so nothing was changed.")

    live = backup_service.database_file(settings)
    try:
        source = sqlite3.connect(f"file:{store.path_of(record.filename)}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(live, timeout=5)
            try:
                target.execute("PRAGMA locking_mode=EXCLUSIVE")
                deadline = time.monotonic() + IN_USE_WAIT_SECONDS

                def give_up_if_busy(status: int, remaining: int, total: int) -> None:
                    if (
                        time.monotonic() > deadline
                    ):  # the copy retries a busy database forever unless told to stop
                        raise sqlite3.OperationalError("database is in use")

                source.backup(
                    target, progress=give_up_if_busy
                )  # fails (and changes nothing) if the database is in use
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.Error as exc:
        observability.log_event(
            "backup", "restore copy failed", backup_key=record.backup_key, reason=str(exc)[:120]
        )  # SQLite's own words: no path, no data
        result = RestoreResult(
            False,
            RestoreMode.RESTORE,
            record.backup_key,
            "The database is in use or could not be written. Stop the application and try again. Nothing was changed.",
            pre_restore_key=safety.key,
        )
        _log(store, result, actor)
        return result
    after = backup_service.verify_file(live)
    detail = "The database was restored from the backup."
    if verification.compatibility == "upgradable":
        detail += " Run the migrations (alembic upgrade head) before starting the application."
    result = RestoreResult(
        after.ok,
        RestoreMode.RESTORE,
        record.backup_key,
        detail if after.ok else "The restored database failed its checks. Restore the safety backup.",
        after,
        safety.key,
    )
    _log(store, result, actor)
    return result


def record_attempt(session, result: RestoreResult, actor: str) -> None:  # noqa: ANN001
    """Store a validation or rehearsal in `restore_records` (a real restore is written after the swap: `note_in_database`)."""
    from app.models import RestoreRecord

    session.add(
        RestoreRecord(
            backup_key=result.backup_key,
            mode=result.mode,
            status="OK" if result.ok else "FAILED",
            initiated_by=actor,
            detail=result.detail[:300],
        )
    )


def note_in_database(result: RestoreResult, actor: str, database: Path) -> None:
    """After a restore, write the attempt into the (new) live database's `restore_records`, if it has that table."""
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute(
                "INSERT INTO restore_records (backup_key, mode, status, initiated_by, detail, attempts, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (
                    result.backup_key,
                    result.mode.value,
                    "OK" if result.ok else "FAILED",
                    actor,
                    result.detail[:300],
                    utc_now().isoformat(sep=" "),
                ),
            )
    except sqlite3.Error:
        pass
