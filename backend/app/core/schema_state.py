"""Is the database at the migration the code expects? Used by readiness and by backup/restore compatibility checks."""

from functools import lru_cache

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import BACKEND_DIR


@lru_cache
def code_head() -> str:
    """The newest migration revision that ships with this code."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


@lru_cache
def known_revisions() -> tuple[str, ...]:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return tuple(r.revision for r in ScriptDirectory.from_config(config).walk_revisions())


def database_revision(connection: Connection) -> str | None:
    """The migration revision the connected database is at, or None if it has none."""
    try:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001  (no table: an empty or foreign database)
        return None
