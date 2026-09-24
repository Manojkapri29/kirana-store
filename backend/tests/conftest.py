"""Shared fixtures.

Every database test runs against a SQLite file that was created by the real Alembic migration (not by
`metadata.create_all`), so the tests exercise exactly the schema that ships. The migration runs once per
test session; each test gets its own private copy of the migrated file.
"""

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Argon2 at its production cost takes ~100 ms a hash; tests hash a lot, so they use the cheapest legal setting.
# (Set before the application is imported: importing it reads the settings.)
os.environ.setdefault("KIRANA_PASSWORD_HASH_TIME_COST", "1")
os.environ.setdefault("KIRANA_PASSWORD_HASH_MEMORY_KIB", "1024")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_request_context
from app.core.config import BACKEND_DIR
from app.core.context import RequestContext
from app.db import session as session_module
from app.db.engine import create_db_engine
from app.main import create_app
from app.models import Category, Shop, User
from app.models.enums import UserRole
from tests import factories  # noqa: E402


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


# Set KIRANA_TEST_POSTGRES_URL=postgresql+psycopg://user@host:port/postgres to run the suite on a real PostgreSQL server instead of SQLite:
# one template database is created and migrated with Alembic, and every test gets its own copy (CREATE DATABASE ... TEMPLATE).
POSTGRES_ADMIN_URL = os.environ.get("KIRANA_TEST_POSTGRES_URL")


def _pg_admin():  # noqa: ANN202
    from sqlalchemy import create_engine

    return create_engine(POSTGRES_ADMIN_URL, isolation_level="AUTOCOMMIT")


def _pg_url(name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(POSTGRES_ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ANN201
    """Tests that exercise SQLite-only machinery (the file backup API, PRAGMAs, a database *file*) are skipped on PostgreSQL."""
    if not POSTGRES_ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="SQLite-specific: exercises a SQLite file, PRAGMA or the SQLite backup API")
    for item in items:
        if Path(str(item.fspath)).name in SQLITE_ONLY_MODULES or any(part in item.nodeid for part in SQLITE_ONLY_NODES):
            item.add_marker(skip)


# Modules that are about SQLite itself: its file backup API and restore, its PRAGMAs, EXPLAIN QUERY PLAN index checks, and bulk loads through a
# raw SQLite connection. (PostgreSQL has its own backup tools and EXPLAIN; those are not covered by this suite.)
SQLITE_ONLY_MODULES = {
    "test_phase11_backup.py", "test_followup_backup_files.py", "test_phase19_restore_drill.py", "test_database_engine.py",
}  # fmt: skip
# Individual tests that build or inspect a SQLite database file (migration data tests) or read SQLite's storage types.
SQLITE_ONLY_NODES = (
    "test_phase11_performance.py::TestScreensStayQuick", "test_phase16_performance.py::TestIndexesServeTheAnalyticsQueries",
    "test_promotions.py::TestMigration0009", "test_quick_sales.py::TestMigration0008", "test_phase9_migrations.py",
    "test_types.py::TestMoneyStoredInDatabase", "test_types.py::TestUtcDateTimeType::test_aware_datetime_in_another_timezone_is_stored_as_utc",
    "test_phase12_ops.py::TestJobs::test_the_backup_job_makes_one_verified_backup",
    "test_image_intelligence.py::TestPlanAndPrivacy::test_the_photo_bytes_never_appear_in_the_database_or_the_logs",
)  # fmt: skip


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path | str:
    if POSTGRES_ADMIN_URL:
        name = f"kirana_tpl_{os.getpid()}"
        with _pg_admin().connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{name}"'))
        command.upgrade(alembic_config(_pg_url(name)), "head")
        yield name
        with _pg_admin().connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        return
    path = tmp_path_factory.mktemp("template") / "template.db"
    command.upgrade(alembic_config(sqlite_url(path)), "head")
    # Fold the WAL file into the main file so that copying the single file is a complete snapshot.
    engine = create_db_engine(sqlite_url(path))
    with engine.connect() as connection:
        connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    engine.dispose()
    yield path


_pg_counter = iter(range(1, 10**9))


@pytest.fixture
def db_url(migrated_template: Path | str, tmp_path: Path) -> Iterator[str]:
    if POSTGRES_ADMIN_URL:
        name = f"kirana_t_{os.getpid()}_{next(_pg_counter)}"
        with _pg_admin().connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{migrated_template}"'))
        yield _pg_url(name)
        with _pg_admin().connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        return
    target = tmp_path / "test.db"
    shutil.copy(migrated_template, target)
    yield sqlite_url(target)


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


_PG_WORDING = {
    "FOREIGN KEY": "foreign key constraint",
    "UNIQUE": "unique constraint|duplicate key",
    "NOT NULL": "not-null constraint|null value",
    "CHECK": "check constraint",
}


def _wording(match: str | None) -> str | None:
    """SQLite and PostgreSQL word the same refusal differently; on PostgreSQL the generic SQLite words also accept PostgreSQL's."""
    if match is None or not POSTGRES_ADMIN_URL:
        return match
    for sqlite_word, pg_word in _PG_WORDING.items():
        match = match.replace(sqlite_word, f"(?:{sqlite_word}|{pg_word})")
    return match


def assert_rejected(session: Session, *objects: object, match: str | None = None) -> None:
    match = _wording(match)
    """Assert that the database refuses to store `objects`, then reset the session."""
    session.add_all(objects)
    with pytest.raises(IntegrityError, match=match):
        session.flush()
    session.rollback()


def assert_sql_rejected(
    session: Session, sql: str, params: dict | None = None, match: str | None = None
) -> None:
    match = _wording(match)
    # PostgreSQL reports a trigger's refusal (insert-only ledgers) as a ProgrammingError, SQLite as an IntegrityError.
    with pytest.raises(DBAPIError if POSTGRES_ADMIN_URL else IntegrityError, match=match):
        session.execute(text(sql), params or {})
    session.rollback()


# --- API fixtures ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _rate_limits_off_by_default(monkeypatch: pytest.MonkeyPatch):
    """Most tests fire many requests in a second; the rate-limit tests turn the limiter back on themselves."""
    from app.core import ratelimit
    from app.core.config import get_settings

    monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    ratelimit.limiter.reset()
    yield
    get_settings.cache_clear()


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


@pytest.fixture
def give_plan(session_factory: sessionmaker[Session]):
    """Put a shop on a plan: give_plan(tenant, "pro"). Shops with no subscription are on the Free plan."""
    from app.services import entitlement_service

    def _give(tenant: Tenant, code: str, **kwargs: object) -> None:
        with session_factory() as new_session, new_session.begin():
            entitlement_service.assign_plan(new_session, tenant.shop.id, code, **kwargs)

    return _give


# --- Real sign-in (no dependency override): the application exactly as it runs -------------------------------------------


@pytest.fixture
def real_client(monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]):
    """A factory of API clients that use the real session-cookie authentication. Each client is its own browser."""
    monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
    clients: list[TestClient] = []

    def _make() -> TestClient:
        client = TestClient(create_app())
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


class SignedIn(TestClient):
    """A client that has signed in: it sends the CSRF header on its own, like the app does."""


@pytest.fixture
def sign_in(real_client):
    def _sign_in(
        email: str, password: str = factories.PASSWORD, shop_user_id: int | None = None
    ) -> TestClient:
        client = real_client()
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        body = response.json()
        client.headers["X-CSRF-Token"] = body["csrf_token"]
        if shop_user_id is not None and body["active_user_id"] != shop_user_id:
            chosen = client.post("/api/v1/auth/select-shop", json={"user_id": shop_user_id})
            assert chosen.status_code == 200, chosen.text
        client.session_info = body  # type: ignore[attr-defined]
        return client

    return _sign_in


@pytest.fixture
def staff_of(session_factory):
    """Create a member of a shop with a system role and return their email: staff_of(tenant_a, "CASHIER")."""

    def _make(tenant: Tenant, role_code: str, email: str | None = None, status: str = "ACTIVE") -> str:
        with session_factory() as s, s.begin():
            account, _ = factories.make_login(s, tenant.shop, role_code, email, status=status)
            return account.email

    return _make


@pytest.fixture
def owner_login(session_factory, tenant_a: Tenant) -> str:
    """Tenant A's owner can sign in. Returns the email."""
    with session_factory() as s, s.begin():
        user = s.get(User, tenant_a.user.id)
        return factories.link_owner(s, user).email
