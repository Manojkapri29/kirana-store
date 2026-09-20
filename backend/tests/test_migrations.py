"""Alembic: the database can be built from scratch, is in sync with the models, and can be rolled back."""

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.core.config import BACKEND_DIR
from app.db.engine import create_db_engine
from app.models import Base
from tests.conftest import alembic_config, sqlite_url

EXPECTED_TABLES = {
    "audit_log", "categories", "customer_ledger", "customers", "document_sequences",
    "expense_categories", "expenses", "idempotency_keys", "inventory_transactions", "products",
    "purchase_items", "purchase_return_items", "purchase_returns", "purchases", "quick_sales",
    "sale_items", "sales", "sales_return_items", "sales_returns", "shops", "suppliers", "units", "users",
}  # fmt: skip

MIGRATION_FILE = BACKEND_DIR / "migrations" / "versions" / "0001_initial_schema.py"


@pytest.fixture
def blank_db_url(tmp_path: Path) -> str:
    return sqlite_url(tmp_path / "blank.db")


def table_names(url: str) -> set[str]:
    engine = create_db_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def current_revision(url: str) -> str | None:
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_upgrade_from_an_empty_database_creates_every_table(blank_db_url: str):
    assert table_names(blank_db_url) == set()

    command.upgrade(alembic_config(blank_db_url), "head")

    assert table_names(blank_db_url) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == len(EXPECTED_TABLES)


def test_migration_state_is_at_head_and_matches_the_models(blank_db_url: str):
    config = alembic_config(blank_db_url)
    command.upgrade(config, "head")

    head = ScriptDirectory.from_config(config).get_current_head()
    assert head == "0001"
    assert current_revision(blank_db_url) == head

    # `alembic check` raises if autogenerate would produce any change (models and DB disagree).
    command.check(config)


def test_there_is_a_single_migration_head():
    heads = ScriptDirectory.from_config(alembic_config("sqlite:///unused.db")).get_heads()
    assert heads == ["0001"]


def test_downgrade_removes_everything_and_upgrade_can_run_again(blank_db_url: str):
    config = alembic_config(blank_db_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")
    assert table_names(blank_db_url) == set()
    engine = create_db_engine(blank_db_url)
    with engine.connect() as connection:
        leftover = connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'"))
    engine.dispose()
    assert leftover == 0

    command.upgrade(config, "head")
    assert table_names(blank_db_url) == EXPECTED_TABLES


def test_units_are_seeded_by_the_migration(session):
    rows = session.execute(text("SELECT code, name, allows_decimal FROM units ORDER BY id")).all()

    assert [tuple(row) for row in rows] == [
        ("pcs", "Piece", 0),
        ("kg", "Kilogram", 1),
        ("g", "Gram", 0),
        ("L", "Litre", 1),
        ("ml", "Millilitre", 0),
        ("pkt", "Packet", 0),
        ("box", "Box", 0),
        ("doz", "Dozen", 1),
    ]


def test_migration_is_self_contained():
    """Migrations must not import application code, or they would break when the models change."""
    source = MIGRATION_FILE.read_text()

    assert not re.search(r"^\s*(from|import)\s+app\b", source, flags=re.MULTILINE)


def test_migration_renders_valid_looking_postgresql_ddl():
    """PostgreSQL is not installed in Phase 2, but the migration is rendered as PostgreSQL SQL (offline,
    no server needed) to catch SQLite-only constructs early. The full run on a real PostgreSQL server
    happens at the checkpoint after Phase 10."""
    config = alembic_config("postgresql://user:password@localhost/kirana")
    buffer = io.StringIO()
    config.output_buffer = buffer

    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()

    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE {table} " in sql
    assert "BIGSERIAL" in sql  # auto-increment ids
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "DEFAULT false" in sql and "DEFAULT true" in sql
    assert not re.search(r"BOOLEAN DEFAULT [01]\b", sql), "boolean defaults must not be SQLite-style 0/1"
    assert "CREATE FUNCTION kirana_forbid_change()" in sql
    assert sql.count("CREATE TRIGGER") == 3
    assert sql.count("INSERT INTO units") == 8
    assert "AUTOINCREMENT" not in sql
