"""Alembic migration environment.

* The database URL comes from configuration (`KIRANA_DATABASE_URL`) unless a caller sets
  `sqlalchemy.url` on the Alembic config (the tests do, to migrate a temporary database).
* Migrations run on an engine built by `create_db_engine`, so SQLite gets the same settings
  (foreign keys, WAL, busy timeout, explicit transactions) as the application.
* Migration files must stay self-contained: they use plain SQLAlchemy types, never `app.*` imports, so an
  old migration keeps working after the models change. `render_item` below makes autogenerate follow that.
"""

from alembic import context

from app.core.config import get_settings
from app.db.engine import BEGIN_IMMEDIATE_OPTION, create_db_engine
from app.db.types import Money, Quantity, UTCDateTime
from app.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Write our custom column types into migrations as the plain types they are stored as."""
    if type_ == "type":
        if isinstance(obj, Money | Quantity):
            return "sa.BigInteger()"
        if isinstance(obj, UTCDateTime):
            return "sa.DateTime(timezone=True)"
    return False  # default rendering


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_item=render_item,
        compare_type=True,
        # SQLite cannot ALTER most things in place; batch mode rebuilds the table instead.
        render_as_batch=True,
        # Run each migration atomically. SQLite supports transactional DDL; Alembic is cautious about it.
        transactional_ddl=True,
        transaction_per_migration=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Foreign keys are OFF while migrating (see create_db_engine) and verified afterwards.
    engine = create_db_engine(_database_url(), enforce_foreign_keys=False)
    try:
        with engine.connect() as connection:
            # Start migration transactions with BEGIN IMMEDIATE on SQLite so nothing can interleave.
            connection = connection.execution_options(**{BEGIN_IMMEDIATE_OPTION: True})
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
        if engine.dialect.name == "sqlite":
            _assert_no_foreign_key_violations(engine)
    finally:
        engine.dispose()


def _assert_no_foreign_key_violations(engine) -> None:  # type: ignore[no-untyped-def]
    """A migration that rebuilt a table must not leave a row pointing at something that no longer exists."""
    with engine.connect() as connection:
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(f"Migration left foreign key violations: {violations[:5]}")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
