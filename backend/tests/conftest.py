"""Shared fixtures.

Every database test runs against a SQLite file that was created by the real Alembic migration (not by
`metadata.create_all`), so the tests exercise exactly the schema that ships. The migration runs once per
test session; each test gets its own private copy of the migrated file.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import BACKEND_DIR
from app.db.engine import create_db_engine
from app.models import Category, Shop, User
from tests import factories


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    command.upgrade(alembic_config(sqlite_url(path)), "head")
    # Fold the WAL file into the main file so that copying the single file is a complete snapshot.
    engine = create_db_engine(sqlite_url(path))
    with engine.connect() as connection:
        connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    engine.dispose()
    return path


@pytest.fixture
def db_url(migrated_template: Path, tmp_path: Path) -> str:
    target = tmp_path / "test.db"
    shutil.copy(migrated_template, target)
    return sqlite_url(target)


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@dataclass
class Tenant:
    """One shop with an owner user and a product category, all committed."""

    shop: Shop
    user: User
    category: Category


def _make_tenant(session: Session, name: str) -> Tenant:
    shop = factories.make_shop(session, name)
    user = factories.make_user(session, shop)
    category = factories.make_category(session, shop)
    session.commit()
    return Tenant(shop=shop, user=user, category=category)


@pytest.fixture
def tenant_a(session: Session) -> Tenant:
    return _make_tenant(session, "Shop A")


@pytest.fixture
def tenant_b(session: Session) -> Tenant:
    return _make_tenant(session, "Shop B")


def assert_rejected(session: Session, *objects: object, match: str | None = None) -> None:
    """Assert that the database refuses to store `objects`, then reset the session."""
    session.add_all(objects)
    with pytest.raises(IntegrityError, match=match):
        session.flush()
    session.rollback()


def assert_sql_rejected(
    session: Session, sql: str, params: dict | None = None, match: str | None = None
) -> None:
    with pytest.raises(IntegrityError, match=match):
        session.execute(text(sql), params or {})
    session.rollback()
