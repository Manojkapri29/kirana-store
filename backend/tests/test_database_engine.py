"""Connection, SQLite settings, and transaction behaviour."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import BACKEND_DIR, Settings
from app.db import session as session_module
from app.db.engine import _resolve_sqlite_url, create_db_engine
from app.db.session import get_write_session, read_session, write_transaction
from app.models import Shop
from tests import factories


def shop_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Shop))


class TestConnectionAndPragmas:
    def test_can_connect_and_query(self, session: Session):
        assert session.scalar(text("SELECT 1")) == 1

    def test_foreign_keys_are_enforced(self, session: Session):
        assert session.scalar(text("PRAGMA foreign_keys")) == 1

    def test_wal_journal_mode(self, session: Session):
        assert session.scalar(text("PRAGMA journal_mode")).lower() == "wal"

    def test_busy_timeout_comes_from_configuration(self, db_url: str):
        engine = create_db_engine(db_url, busy_timeout_ms=1234)
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA busy_timeout")) == 1234
        engine.dispose()

    def test_database_url_defaults_and_is_overridable(self, monkeypatch: pytest.MonkeyPatch):
        assert Settings(_env_file=None).database_url == "sqlite:///./data/kirana.db"
        monkeypatch.setenv("KIRANA_DATABASE_URL", "sqlite:///./elsewhere.db")
        assert Settings(_env_file=None).database_url == "sqlite:///./elsewhere.db"

    def test_relative_sqlite_paths_are_anchored_to_the_backend_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        monkeypatch.setattr("app.db.engine.BACKEND_DIR", tmp_path)

        resolved = _resolve_sqlite_url(make_url("sqlite:///./data/kirana.db"))

        assert resolved.database == str(tmp_path / "data" / "kirana.db")
        assert (tmp_path / "data").is_dir()  # the folder is created for a fresh checkout

    def test_backend_dir_is_the_backend_folder(self):
        assert (BACKEND_DIR / "alembic.ini").is_file()


class TestWriteTransactions:
    def test_commits_on_success(self, session_factory: sessionmaker[Session]):
        with write_transaction(session_factory) as session:
            factories.make_shop(session, "Committed")

        with read_session(session_factory) as session:
            assert shop_count(session) == 1

    def test_rolls_back_when_an_exception_escapes(self, session_factory: sessionmaker[Session]):
        with pytest.raises(RuntimeError):
            with write_transaction(session_factory) as session:
                factories.make_shop(session, "Never saved")
                raise RuntimeError("boom")

        with read_session(session_factory) as session:
            assert shop_count(session) == 0

    def test_multi_step_write_is_all_or_nothing(self, session_factory: sessionmaker[Session]):
        """A failure in the second step must undo the first: the basis for safe stock updates."""
        with pytest.raises(Exception):  # noqa: B017 - any database error will do
            with write_transaction(session_factory) as session:
                factories.make_shop(session, "First step")
                session.add(
                    Shop(name="   ", business_type="OTHER", phone="1", address="x")
                )  # violates a CHECK constraint
                session.flush()

        with read_session(session_factory) as session:
            assert shop_count(session) == 0

    def test_read_session_never_persists_changes(self, session_factory: sessionmaker[Session]):
        with read_session(session_factory) as session:
            factories.make_shop(session, "Ephemeral")

        with read_session(session_factory) as session:
            assert shop_count(session) == 0

    def test_write_transaction_takes_the_write_lock_immediately(self, db_url: str):
        """BEGIN IMMEDIATE: a second writer is refused at the START of its transaction, before it has
        read anything. That is what prevents two sales from both seeing the last unit as available."""
        engine_a = create_db_engine(db_url, busy_timeout_ms=100)
        engine_b = create_db_engine(db_url, busy_timeout_ms=100)
        factory_a = sessionmaker(bind=engine_a, expire_on_commit=False)
        factory_b = sessionmaker(bind=engine_b, expire_on_commit=False)

        with write_transaction(factory_a):
            with pytest.raises(OperationalError, match="locked"):
                with write_transaction(factory_b):
                    pytest.fail("the second writer must not get inside the transaction")

        # Once the first writer is done, the second one can proceed.
        with write_transaction(factory_b) as session:
            factories.make_shop(session, "Second writer")

        engine_a.dispose()
        engine_b.dispose()

    def test_readers_are_not_blocked_while_a_write_is_in_progress(self, db_url: str):
        engine_a = create_db_engine(db_url, busy_timeout_ms=100)
        engine_b = create_db_engine(db_url, busy_timeout_ms=100)
        factory_a = sessionmaker(bind=engine_a, expire_on_commit=False)
        factory_b = sessionmaker(bind=engine_b, expire_on_commit=False)

        with write_transaction(factory_a) as writer:
            factories.make_shop(writer, "Uncommitted")
            with read_session(factory_b) as reader:
                # WAL: the reader sees the last committed state and is not blocked.
                assert shop_count(reader) == 0

        engine_a.dispose()
        engine_b.dispose()

    def test_fastapi_write_dependency_commits_and_rolls_back(
        self, monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
    ):
        monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)

        committed = get_write_session()
        factories.make_shop(next(committed), "Via dependency")
        with pytest.raises(StopIteration):
            next(committed)  # request finished normally -> commit

        failed = get_write_session()
        factories.make_shop(next(failed), "Rolled back")
        with pytest.raises(RuntimeError):
            failed.throw(RuntimeError("request failed"))  # request failed -> rollback

        with read_session(session_factory) as session:
            names = set(session.scalars(select(Shop.name)))
        assert names == {"Via dependency"}


class TestUtcTimestamps:
    def test_timestamps_are_timezone_aware_utc(self, session: Session):
        shop = factories.make_shop(session)
        session.commit()
        session.refresh(shop)

        assert shop.created_at.tzinfo is UTC
        assert abs((datetime.now(UTC) - shop.created_at).total_seconds()) < 60

    def test_updated_at_moves_forward_on_update(self, session: Session):
        shop = factories.make_shop(session)
        session.commit()
        first = shop.updated_at

        shop.name = "Renamed"
        session.commit()

        assert shop.updated_at > first
