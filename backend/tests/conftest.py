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
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_request_context
from app.core.config import BACKEND_DIR
from app.core.context import RequestContext
from app.db import session as session_module
from app.db.engine import create_db_engine
from app.main import create_app
from app.models import Category, Shop, User
from app.models.enums import UserRole
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


def _make_tenant(session: Session, name: str, business_type: str = "GROCERY") -> Tenant:
    shop = factories.make_shop(session, name, business_type)
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


@pytest.fixture
def tenant_of(session: Session):
    """Create a shop of a chosen business type: tenant_of("BAKERY"). Names are unique per call."""
    created: list[Tenant] = []

    def _make(business_type: str) -> Tenant:
        tenant = _make_tenant(session, f"{business_type.title()} Shop {len(created)}", business_type)
        # the shop's user needs a unique email; make_user derives it from the shop id, which is unique
        created.append(tenant)
        return tenant

    return _make


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


# --- API fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]):
    """Build an API client that acts as the given tenant's user (replacing the development login).

    Several clients can coexist, each bound to a different shop, which is how isolation is tested.
    """
    monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
    clients: list[TestClient] = []

    def _make(tenant: Tenant, role: UserRole = UserRole.OWNER) -> TestClient:
        app = create_app()
        context = RequestContext(shop_id=tenant.shop.id, user_id=tenant.user.id, role=role)
        app.dependency_overrides[get_request_context] = lambda: context
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client_a(make_client, tenant_a: Tenant) -> TestClient:
    return make_client(tenant_a)


@pytest.fixture
def client_b(make_client, tenant_b: Tenant) -> TestClient:
    return make_client(tenant_b)


@pytest.fixture
def units(session: Session) -> dict[str, int]:
    """Unit code -> id, e.g. units["kg"]."""
    return {code: unit_id for unit_id, code in session.execute(text("SELECT id, code FROM units"))}


@pytest.fixture
def fresh(session_factory: sessionmaker[Session]):
    """Run a query in a brand-new session, so a test never reads a stale snapshot."""

    def _run(fn):
        with session_factory() as new_session:
            return fn(new_session)

    return _run


@pytest.fixture
def set_shop(session_factory: sessionmaker[Session]):
    """Change shop settings directly in the database: set_shop(tenant, allow_negative_stock=True)."""

    def _set(tenant: Tenant, **values: object) -> None:
        with session_factory() as new_session:
            new_session.execute(update(Shop).where(Shop.id == tenant.shop.id).values(**values))
            new_session.commit()

    return _set


def context_for(tenant: Tenant, role: UserRole = UserRole.OWNER) -> RequestContext:
    return RequestContext(shop_id=tenant.shop.id, user_id=tenant.user.id, role=role)
