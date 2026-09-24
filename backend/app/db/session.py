"""Sessions and transactions.

Two ways to get a session:

* `get_session` / `read_session`: for reading. Uses a plain transaction; never commits.
* `write_transaction` / `get_write_session`: for anything that changes data. One transaction that is
  committed if the block finishes normally and rolled back if it raises. On SQLite the transaction is
  opened with `BEGIN IMMEDIATE`, which serializes writers.

Rule for services (Phases 3+): one business action = one `write_transaction`. The stock check and the
ledger insert must happen inside the same one, or two simultaneous sales could both take the last item.
Never call `session.commit()` yourself inside a service; the transaction owner commits.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import BEGIN_IMMEDIATE_OPTION, get_engine


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit, so services can return them.
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def read_session(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    session = (factory or get_session_factory())()
    try:
        yield session
    finally:
        session.rollback()  # reading never leaves a transaction open
        session.close()


@contextmanager
def write_transaction(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """A write transaction: commit on success, roll back on any exception."""
    session = (factory or get_session_factory())()
    try:
        # Acquiring the connection with this option starts the transaction as BEGIN IMMEDIATE.
        session.connection(execution_options={BEGIN_IMMEDIATE_OPTION: True})
        yield session
        session.commit()
    except BaseException:
        pending = session.info.pop(AFTER_ROLLBACK_KEY, None)
        session.rollback()
        if pending:
            _record_after_rollback(session, pending)
        raise
    finally:
        session.close()


AFTER_ROLLBACK_KEY = "after_rollback"


def note_after_rollback(session: Session, action: Callable[[Session], None]) -> None:
    """Ask for `action(session)` to run in a fresh transaction if THIS one is rolled back. For facts that must
    outlive a refusal (for example "someone tried to change a closed period"): the refusal rolls the request
    back, and without this the record of the attempt would go with it. It never runs on success."""
    session.info.setdefault(AFTER_ROLLBACK_KEY, []).append(action)


def _record_after_rollback(session: Session, actions: list[Callable[[Session], None]]) -> None:
    try:
        for action in actions:
            action(session)
        session.commit()
    except Exception:  # noqa: BLE001 - a failure to note the attempt must never hide the original error
        session.rollback()


def get_session() -> Iterator[Session]:
    """FastAPI dependency for read-only endpoints."""
    with read_session() as session:
        yield session


def get_write_session() -> Iterator[Session]:
    """FastAPI dependency for endpoints that change data (commits when the request succeeds)."""
    with write_transaction() as session:
        yield session
