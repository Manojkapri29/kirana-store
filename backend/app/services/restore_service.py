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
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core import observability
from app.core.config import BACKEND_DIR, Settings, get_settings
from app.db.types import utc_now
from app.models import BackupRecord
from app.models.enums import BackupKind, BackupStatus, RestoreMode
from app.services import backup_service, pg_backup
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
    settings = get_settings()
    if pg_backup.is_postgres(settings):
        return _rehearse_postgres(record, actor, store, settings)
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


def _rehearse_postgres(record: BackupRecord, actor: str, store: BackupStorage, settings: Settings) -> RestoreResult:
    """Restore the archive into a scratch database, count the main tables, drop it. The live database is not touched."""
    path = store.path_of(record.filename or "")
    first = backup_service.verify_file(path, record.sha256)
    if not first.ok:
        result = RestoreResult(False, RestoreMode.REHEARSAL, record.backup_key, "The backup did not pass its checks.", first)
    else:
        try:
            report = pg_backup.rehearse(settings, path)
            ok = first.compatibility == "current"
            detail = "The backup restores cleanly." if ok else "The backup restores, but it is from an older schema: run the migrations after restoring."
            result = RestoreResult(ok or first.compatibility == "upgradable", RestoreMode.REHEARSAL, record.backup_key, detail, first, report=report)
        except (RuntimeError, pg_backup.PgToolsMissing, OSError, subprocess.SubprocessError):
            result = RestoreResult(
                False, RestoreMode.REHEARSAL, record.backup_key,
                "The trial restore failed. It needs pg_restore and a database role that may create a scratch database.", first,
            )  # fmt: skip
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

    if pg_backup.is_postgres(settings):
        return _restore_postgres(record, actor, store, settings, verification, safety)
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
    photos_note = _restore_photos(record.backup_key, store, settings)
    if photos_note:
        detail += " " + photos_note
    _reregister_backups(live, store, settings)
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


def _restore_postgres(
    record: BackupRecord, actor: str, store: BackupStorage, settings: Settings, verification: Verification, safety
) -> RestoreResult:  # noqa: ANN001
    try:
        pg_backup.restore_into_live(settings, store.path_of(record.filename or ""))
    except (RuntimeError, pg_backup.PgToolsMissing, OSError, subprocess.SubprocessError):
        result = RestoreResult(
            False, RestoreMode.RESTORE, record.backup_key,
            "The database could not be restored (is the application stopped?). The restore is all-or-nothing, so nothing was changed.",
            pre_restore_key=safety.key,
        )  # fmt: skip
        _log(store, result, actor)
        return result
    revision = _live_revision(settings)
    ok = revision is not None and revision == verification.schema_revision
    after = Verification(ok, {"restored": "ok" if ok else "revision_mismatch"}, revision, backup_service.compatibility_of(revision))
    detail = "The database was restored from the backup."
    if verification.compatibility == "upgradable":
        detail += " Run the migrations (alembic upgrade head) before starting the application."
    photos_note = _restore_photos(record.backup_key, store, settings)
    if photos_note:
        detail += " " + photos_note
    _reregister_backups(None, store, settings)
    result = RestoreResult(ok, RestoreMode.RESTORE, record.backup_key, detail if ok else "The restored database failed its checks. Restore the safety backup.", after, safety.key)
    _log(store, result, actor)
    return result


def _live_revision(settings: Settings) -> str | None:
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        return None
    finally:
        engine.dispose()


def _restore_photos(key: str, store: BackupStorage, settings: Settings) -> str:
    """Put the backup's photos back next to the restored database. Only valid photo names are extracted (nothing can leave the folder),
    an existing photo is never overwritten, and a photo file the backup does not hold is never deleted."""
    manifest_path = store.path_of(f"{key}.json")
    try:
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    except (OSError, ValueError):
        manifest = {}
    name = manifest.get("images_file")
    if not name:
        return "This backup holds no photos." if manifest else ""
    folder = backup_service.image_folder(settings)
    archive = store.path_of(name)
    if folder is None or not archive.is_file():
        return "The backup's photos could not be restored: restore the photo folder from its own backup."
    if manifest.get("images_sha256") and backup_service._sha256(archive) != manifest["images_sha256"]:
        return "The backup's photo archive failed its checksum, so no photos were restored."
    restored = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or not backup_service.IMAGE_MEMBER.match(member.name):
                    continue
                target = folder / member.name
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                temp = target.with_suffix(target.suffix + ".part")
                temp.write_bytes(source.read())
                temp.replace(target)
                restored += 1
    except (OSError, tarfile.TarError):
        return "The backup's photos could not be fully restored: restore the photo folder from its own backup."
    return f"{restored} photo(s) restored."


def _manifests(store: BackupStorage) -> list[dict]:
    """Every backup that still has a manifest and its file beside it."""
    folder = store.directory()
    found = []
    for manifest_path in sorted(folder.glob("*.json")):
        try:
            m = json.loads(manifest_path.read_text())
            key, filename = m["backup_key"], m["filename"]
        except (OSError, ValueError, KeyError):
            continue
        if (folder / filename).is_file():
            found.append({**m, "backup_key": key, "filename": filename})
    return found


def _reregister_backups(database: Path | None, store: BackupStorage, settings: Settings | None = None) -> None:
    """The list of backups lives in the database, so restoring an older backup forgets newer ones. Every backup file that still has a
    manifest beside it is written back into the restored database's list (a backup already listed is left alone)."""
    settings = settings or get_settings()
    now = utc_now().isoformat(sep=" ")[:26]  # the format the database stores (UTC, no offset)
    insert = (
        "INSERT INTO backup_records (backup_key, kind, status, storage_provider, filename, size_bytes, sha256, schema_revision,"
        " initiated_by, verified_at, updated_at, created_at) VALUES (:key, :kind, 'VERIFIED', :provider, :filename, :size, :sha,"
        " :revision, :by, :now, :now, :created)"
    )

    def row(m: dict) -> dict:
        return {
            "key": m["backup_key"], "kind": m.get("kind", "MANUAL"), "provider": store.name, "filename": m["filename"],
            "size": m.get("size_bytes"), "sha": m.get("sha256"), "revision": m.get("schema_revision"),
            "by": m.get("initiated_by", "unknown"), "now": now, "created": (m.get("created_at") or now).replace("T", " ")[:26],
        }  # fmt: skip

    try:
        if pg_backup.is_postgres(settings):
            from sqlalchemy import create_engine, text

            engine = create_engine(settings.database_url)
            try:
                with engine.begin() as connection:
                    known = {r[0] for r in connection.execute(text("SELECT backup_key FROM backup_records"))}
                    for m in _manifests(store):
                        if m["backup_key"] not in known:
                            created = datetime.fromisoformat(m["created_at"]) if m.get("created_at") else utc_now()
                            connection.execute(text(insert), {**row(m), "now": utc_now(), "created": created})
            finally:
                engine.dispose()
            return
        assert database is not None
        with sqlite3.connect(database, timeout=5) as connection:
            known = {r[0] for r in connection.execute("SELECT backup_key FROM backup_records")}
            for m in _manifests(store):
                if m["backup_key"] not in known:
                    connection.execute(insert, row(m))
    except Exception:  # noqa: BLE001
        pass  # the restore itself succeeded; the list can be rebuilt from the manifests later


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


def record_restore(result: RestoreResult, actor: str, settings: Settings | None = None) -> None:
    """After a restore, write the attempt into the restored database (SQLite file or PostgreSQL)."""
    settings = settings or get_settings()
    if not pg_backup.is_postgres(settings):
        note_in_database(result, actor, backup_service.database_file(settings))
        return
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO restore_records (backup_key, mode, status, initiated_by, detail, attempts, created_at)"
                    " VALUES (:key, :mode, :status, :by, :detail, 1, :now)"
                ),
                {"key": result.backup_key, "mode": result.mode.value, "status": "OK" if result.ok else "FAILED", "by": actor, "detail": result.detail[:300], "now": utc_now()},
            )
    except Exception:  # noqa: BLE001
        pass
    finally:
        engine.dispose()


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
