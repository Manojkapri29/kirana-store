"""PostgreSQL backups with pg_dump/pg_restore. These run only against a real PostgreSQL server: set KIRANA_TEST_POSTGRES_URL (see conftest.py)."""

import gc
import json

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.services import backup_service, pg_backup, restore_service
from tests.conftest import POSTGRES_ADMIN_URL

pytestmark = pytest.mark.skipif(not POSTGRES_ADMIN_URL, reason="needs a PostgreSQL server (KIRANA_TEST_POSTGRES_URL)")


@pytest.fixture
def env(monkeypatch, db_url, tmp_path):
    monkeypatch.setenv("KIRANA_DATABASE_URL", db_url)
    monkeypatch.setenv("KIRANA_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", "1")
    monkeypatch.setenv("KIRANA_IMAGE_STORAGE_DIR", str(tmp_path / "photos"))
    get_settings.cache_clear()
    return tmp_path / "backups"


def _make(session_factory):
    outcome = backup_service.perform_backup(backup_service.BackupKind.MANUAL, "tester")
    assert outcome.ok, outcome.error_message
    with session_factory() as s, s.begin():
        backup_service.record_outcome(s, outcome)
    return outcome


def _customers(engine) -> int:
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM customers")).scalar()


def test_a_dump_is_verified_described_and_recorded(env, tenant_a, session_factory):
    out = _make(session_factory)
    manifest = json.loads((env / f"{out.key}.json").read_text())
    assert out.filename.endswith(".dump") and manifest["engine"] == "postgresql" and manifest["schema_revision"] == out.schema_revision
    assert "postgresql" not in (env / f"{out.key}.json").read_text().replace('"engine": "postgresql"', "")  # no connection string
    v = backup_service.verify_file(env / out.filename, out.sha256)
    assert v.ok and v.compatibility == "current" and v.checks["archive"] == "ok"


def test_a_corrupted_dump_fails_verification(env, tenant_a, session_factory):
    out = _make(session_factory)
    (env / out.filename).write_bytes(b"not a dump")
    assert not backup_service.verify_file(env / out.filename, out.sha256).ok


def test_a_rehearsal_restores_into_a_scratch_database_and_leaves_the_live_one_alone(env, tenant_a, session_factory, engine):
    out = _make(session_factory)
    with session_factory() as s:
        record = backup_service.get_record(s, out.key)
    before = _customers(engine)
    result = restore_service.rehearse(record, "tester")
    assert result.ok and result.report["shops"] == 1 and _customers(engine) == before
    with engine.connect() as c:
        assert not c.execute(text("SELECT count(*) FROM pg_database WHERE datname LIKE 'kirana_rehearsal_%'")).scalar()  # the scratch database is gone


def test_a_restore_brings_back_the_data_and_the_sequences_and_lists_newer_backups(env, tenant_a, session_factory, engine, session):
    from app.models import Customer

    older = _make(session_factory)
    session.add(Customer(shop_id=tenant_a.shop.id, name="After the backup"))
    session.commit()
    newer = _make(session_factory)
    assert _customers(engine) == 1
    with session_factory() as s:
        record = backup_service.get_record(s, older.key)
    session.close()
    gc.collect()
    engine.dispose()
    result = restore_service.restore(record, confirmation=restore_service.confirmation_phrase(older.key), actor="t@test", via_api=False)
    assert result.ok, result.detail
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM customers")).scalar() == 0  # the customer added after that backup is gone
        s.add(Customer(shop_id=tenant_a.shop.id, name="New after restore"))
        s.commit()  # the id sequence still works (no duplicate key)
        keys = {r[0] for r in s.execute(text("SELECT backup_key FROM backup_records"))}
    assert {older.key, newer.key, result.pre_restore_key} <= keys
    assert pg_backup.revision_of(env / older.filename) == older.schema_revision


def test_a_restore_that_cannot_run_changes_nothing(env, tenant_a, session_factory, engine, session):
    out = _make(session_factory)
    (env / out.filename).write_bytes(b"broken")
    with session_factory() as s:
        record = backup_service.get_record(s, out.key)
    result = restore_service.restore(record, confirmation=restore_service.confirmation_phrase(out.key), actor="t@test", via_api=False)
    assert not result.ok
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM shops")).scalar() == 1  # still there
