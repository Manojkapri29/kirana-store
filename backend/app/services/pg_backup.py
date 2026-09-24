"""PostgreSQL backups through the server's own tools (`pg_dump`, `pg_restore`), never by copying files.

Only the standard client tools are used, and they are never given a password on the command line: the connection comes from the
environment variables PGHOST, PGPORT, PGUSER, PGPASSWORD and PGDATABASE built from the configured URL for the one child process. A dump is
a custom-format archive (`<key>.dump`), verified with `pg_restore --list` and by reading the migration revision out of it. A restore
replaces the objects in the live database with `pg_restore --clean --if-exists --single-transaction`, so a failed restore changes nothing;
it needs the application stopped (a running application would hold locks, and the restore gives up rather than wait for them).
"""

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.config import Settings

TIMEOUT_SECONDS = 600
LOCK_TIMEOUT_MS = 5000
_REVISION = re.compile(r"^COPY [^\n]*alembic_version[^\n]*\n(?P<rev>[0-9A-Za-z_]+)\n", re.M)


class PgToolsMissing(Exception):
    """pg_dump / pg_restore are not installed on this machine."""


def is_postgres(settings: Settings) -> bool:
    return make_url(settings.database_url).get_backend_name() == "postgresql"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise PgToolsMissing(name)
    return path


def _url(settings: Settings) -> URL:
    return make_url(settings.database_url)


def _env(url: URL, database: str | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    if url.host:
        env["PGHOST"] = url.host
    if url.port:
        env["PGPORT"] = str(url.port)
    if url.username:
        env["PGUSER"] = url.username
    if url.password:
        env["PGPASSWORD"] = url.password
    env["PGDATABASE"] = database or (url.database or "")
    env["PGCONNECT_TIMEOUT"] = "10"
    env["PGOPTIONS"] = f"-c lock_timeout={LOCK_TIMEOUT_MS}"
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)  # noqa: S603


def tools_available() -> bool:
    return shutil.which("pg_dump") is not None and shutil.which("pg_restore") is not None


def dump(settings: Settings, target: Path) -> None:
    """Write a custom-format dump of the configured database. Raises `RuntimeError` (with no connection detail) if it fails."""
    url = _url(settings)
    result = _run(
        [_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(target)],
        _env(url),
    )
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("pg_dump failed")


def check_archive(path: Path) -> bool:
    """Is this a readable pg_dump archive (its table of contents can be listed)?"""
    result = _run([_tool("pg_restore"), "--list", str(path)], os.environ.copy())
    return result.returncode == 0 and "alembic_version" in result.stdout


def revision_of(path: Path) -> str | None:
    """The migration revision stored in the archive."""
    result = _run(
        [_tool("pg_restore"), "--data-only", "--table=alembic_version", "--file=-", str(path)],
        os.environ.copy(),
    )
    if result.returncode != 0:
        return None
    match = _REVISION.search(result.stdout)
    return match.group("rev") if match else None


def restore_into_live(settings: Settings, path: Path) -> None:
    """Replace the live database's contents with the archive, all or nothing. Raises `RuntimeError` on any failure."""
    url = _url(settings)
    result = _run(
        [
            _tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction",
            "--exit-on-error", "--dbname", url.database or "", str(path),
        ],
        _env(url),
    )  # fmt: skip
    if result.returncode != 0:
        raise RuntimeError("pg_restore failed")


def rehearse(settings: Settings, path: Path) -> dict[str, int]:
    """Restore into a scratch database, count the main tables, and drop it. The live database is only ever read for its address.
    Needs a role that may create databases; otherwise raises `RuntimeError`."""
    url = _url(settings)
    scratch = f"kirana_rehearsal_{secrets.token_hex(4)}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        try:
            result = _run(
                [
                    _tool("pg_restore"),
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--dbname",
                    scratch,
                    str(path),
                ],
                _env(url, scratch),
            )
            if result.returncode != 0:
                raise RuntimeError("pg_restore failed")
            report: dict[str, int] = {}
            copy = create_engine(url.set(database=scratch))
            try:
                with copy.connect() as connection:
                    for table in ("shops", "products", "sales", "purchases", "customers"):
                        report[table] = int(
                            connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
                        )  # noqa: S608  (fixed names)
            finally:
                copy.dispose()
            return report
        finally:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
    except PgToolsMissing:
        raise
    except Exception as exc:
        raise RuntimeError("rehearsal failed") from exc
    finally:
        admin.dispose()
