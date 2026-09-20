"""Engine creation. The database is chosen only by configuration (`KIRANA_DATABASE_URL`).

SQLite needs some care to behave like a real transactional database:

* Foreign keys are OFF by default in SQLite, so we switch them on for every connection.
* WAL journal mode lets readers work while a writer is active.
* `busy_timeout` makes a connection wait for another writer instead of failing immediately.
* Python's sqlite3 driver decides when to start transactions on its own, which makes it impossible to
  control locking. We turn that off and issue `BEGIN` ourselves (see `_configure_sqlite`).
"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL, make_url

from app.core.config import BACKEND_DIR, get_settings

# Execution option that asks for a write transaction (`BEGIN IMMEDIATE` on SQLite).
BEGIN_IMMEDIATE_OPTION = "sqlite_begin_immediate"


def _resolve_sqlite_url(url: URL) -> URL:
    """Anchor relative file paths to backend/ and create the folder for the database file."""
    if url.database in (None, "", ":memory:"):
        return url
    path = Path(url.database)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(path))


def _configure_sqlite(engine: Engine, busy_timeout_ms: int, enforce_foreign_keys: bool) -> None:
    @event.listens_for(engine, "connect")
    def on_connect(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        # Take over transaction control from the sqlite3 driver; see the "begin" listener below.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA foreign_keys={'ON' if enforce_foreign_keys else 'OFF'}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def on_begin(connection: Connection) -> None:
        # Readers use a plain (deferred) BEGIN so they never block anyone under WAL.
        # Writers use BEGIN IMMEDIATE: the write lock is taken up front, so two sales can never
        # interleave their "check stock, then deduct stock" steps. This is the SQLite equivalent of
        # PostgreSQL's SELECT ... FOR UPDATE, which services also request (SQLAlchemy ignores it here).
        if connection.get_execution_options().get(BEGIN_IMMEDIATE_OPTION):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("BEGIN")


def create_db_engine(
    url: str, *, busy_timeout_ms: int = 5000, echo: bool = False, enforce_foreign_keys: bool = True
) -> Engine:
    """Build an engine for `url`. Used by the app, Alembic and the tests.

    `enforce_foreign_keys=False` is for schema migrations only. SQLite cannot alter most table definitions in
    place, so Alembic rebuilds the table, and rebuilding a table that other tables point at is only possible
    with enforcement off (SQLite's documented procedure). Alembic then runs `PRAGMA foreign_key_check` to
    prove nothing was broken. The application itself always enforces foreign keys.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        engine = create_engine(
            _resolve_sqlite_url(parsed),
            echo=echo,
            # FastAPI serves requests from a thread pool, so connections cross threads.
            connect_args={"check_same_thread": False},
        )
        _configure_sqlite(engine, busy_timeout_ms, enforce_foreign_keys)
        return engine
    return create_engine(parsed, echo=echo, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    """The application's engine, created on first use from the configured URL."""
    settings = get_settings()
    return create_db_engine(settings.database_url, busy_timeout_ms=settings.db_busy_timeout_ms)
