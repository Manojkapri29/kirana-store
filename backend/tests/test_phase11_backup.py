"""Backups and restores: consistent, verified, retained by policy, and restored only deliberately."""

import gc
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.types import utc_now
from app.models import BackupRecord, SystemEvent
from app.models.enums import AdminRole, BackupKind, BackupStatus
from app.services import admin_service, backup_service, restore_service
from tests.test_purchases_api import make_product

ADMIN = "/api/v1/admin"


@pytest.fixture
def env(monkeypatch, db_url, tmp_path):
    """Point the application's settings at the test database file and a private backup folder."""
    monkeypatch.setenv("KIRANA_DATABASE_URL", db_url)
    monkeypatch.setenv("KIRANA_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", "1")
    get_settings.cache_clear()
    return tmp_path / "backups"


def make(session_factory, kind=BackupKind.MANUAL, actor="tester"):
    outcome = backup_service.perform_backup(kind, actor)
    with session_factory() as s, s.begin():
        record = backup_service.record_outcome(s, outcome)
    return outcome, record.backup_key


@pytest.fixture
def stocked(client_a, tenant_a, units):
    make_product(client_a, tenant_a, units, "BK1")
    return client_a


class TestCreation:
    def test_a_backup_is_a_verified_file_with_a_manifest_and_a_record(self, env, stocked, session_factory):
        outcome, key = make(session_factory)
        assert (
            outcome.ok
            and outcome.filename == f"{key}.db"
            and outcome.size_bytes > 0
            and len(outcome.sha256) == 64
        )
        assert (env / outcome.filename).exists() and not list(env.glob(".*.partial"))
        manifest = json.loads((env / f"{key}.json").read_text())
        assert (
            manifest["sha256"] == outcome.sha256
            and manifest["schema_revision"] == outcome.schema_revision
            and manifest["kind"] == "MANUAL"
        )
        with session_factory() as s:
            record = backup_service.get_record(s, key)
            assert (
                record.status is BackupStatus.VERIFIED
                and record.verified_at
                and record.initiated_by == "tester"
                and record.filename == f"{key}.db"
            )
            assert record.error_code is None

    def test_neither_the_manifest_nor_the_record_holds_a_credential_or_a_path(
        self, env, stocked, session_factory, db_url
    ):
        _, key = make(session_factory)
        text = (env / f"{key}.json").read_text()
        with session_factory() as s:
            record = backup_service.get_record(s, key)
            record_text = " ".join(str(getattr(record, c.key)) for c in record.__table__.columns)
        for secret in (db_url, "sqlite:///", str(env), "password", "/tmp", "/private"):
            assert secret not in text and secret not in record_text

    def test_the_backup_contains_the_data_and_passes_sqlites_own_checks(self, env, stocked, session_factory):
        outcome, _ = make(session_factory)
        with sqlite3.connect(env / outcome.filename) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT count(*) FROM products").fetchone() == (1,)
            assert (
                db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == outcome.schema_revision
            )

    def test_it_is_consistent_while_writes_are_happening(
        self, env, stocked, session_factory, tenant_a, units
    ):
        stop, errors = threading.Event(), []

        def writer():
            n = 0
            while not stop.is_set():
                try:
                    stocked.post("/api/v1/customers", json={"name": f"Busy {n}"})
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                n += 1

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            outcomes = [backup_service.perform_backup(BackupKind.MANUAL, "busy") for _ in range(3)]
        finally:
            stop.set()
            thread.join()
        assert all(o.ok for o in outcomes) and not errors
        for outcome in outcomes:
            v = backup_service.verify_file(env / outcome.filename, outcome.sha256)
            assert v.ok and v.checks["integrity"] == "ok" and v.checks["foreign_keys"] == "ok"

    def test_not_enough_disk_space_is_a_safe_failure_that_operators_hear_about(
        self, env, stocked, session_factory, monkeypatch, tenant_a
    ):
        monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", str(10**9))
        get_settings.cache_clear()
        outcome, key = make(session_factory)
        assert (
            not outcome.ok
            and outcome.error_code == "disk_space"
            and "free disk space" in outcome.error_message
        )
        assert not list(env.glob("*.db"))
        with session_factory() as s:
            assert backup_service.get_record(s, key).status is BackupStatus.FAILED
            event = s.scalars(select(SystemEvent).where(SystemEvent.category == "backup")).one()
            assert event.code == "disk_space" and event.severity.value == "ERROR"
        # and the shops are told, once per day, without technical detail
        rows = stocked.get("/api/v1/notifications").json()["items"]
        assert [r["event_type"] for r in rows] == ["BACKUP_FAILED"] and "disk" not in rows[0][
            "message"
        ].lower()
        make(session_factory)
        assert stocked.get("/api/v1/notifications").json()["total"] == 1  # the same day: not repeated

    def test_an_unwritable_folder_is_a_failed_outcome_not_a_crash(self, env, stocked, monkeypatch):
        monkeypatch.setattr(
            "app.services.backup_service.LocalBackupStorage.free_bytes",
            lambda self: (_ for _ in ()).throw(OSError("read-only file system")),
        )
        outcome = backup_service.perform_backup(BackupKind.MANUAL, "x")
        assert (
            not outcome.ok and outcome.error_code == "disk_error" and "read-only" not in outcome.error_message
        )

    def test_an_unreachable_postgresql_server_fails_honestly_and_a_storage_that_does_not_exist_is_not_configured(
        self, env, monkeypatch
    ):
        # PostgreSQL is backed up with pg_dump. Here there is no such server, so the backup FAILS (it is never reported as made);
        # without the tools installed it says so.
        outcome = backup_service.perform_backup(
            BackupKind.MANUAL, "x", Settings(_env_file=None, database_url="postgresql+psycopg://u:p@127.0.0.1:1/db")
        )
        assert not outcome.ok and outcome.error_code in ("dump_failed", "tools_missing")
        assert "u:p" not in (outcome.error_message or "")
        assert (
            backup_service.perform_backup(
                BackupKind.MANUAL, "x", Settings(_env_file=None, backup_storage_provider="s3")
            ).error_code
            == "not_configured"
        )
        with pytest.raises(backup_service.BackupNotConfigured):
            backup_service.get_storage(
                Settings(_env_file=None, backup_storage_provider="gcs")
            )  # never silently replaced by local

    @pytest.mark.parametrize("name", ["../evil.db", "a/b.db", ".hidden", "/etc/passwd"])
    def test_the_storage_refuses_names_that_escape_its_folder(self, env, name):
        with pytest.raises(ValueError):
            backup_service.get_storage().path_of(name)


class TestVerification:
    def test_tampering_and_corruption_are_detected(self, env, stocked, session_factory):
        outcome, key = make(session_factory)
        path = env / outcome.filename
        assert backup_service.verify_file(path, outcome.sha256).ok
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        result = backup_service.verify_file(path, outcome.sha256)
        assert not result.ok and result.checks["checksum"] == "mismatch"
        path.write_bytes(b"this is not a database at all")
        assert backup_service.verify_file(path).checks["integrity"] == "unreadable"
        with session_factory() as s, s.begin():
            record = backup_service.get_record(s, key)
            assert not backup_service.verify_record(s, record).ok and record.status is BackupStatus.FAILED
        assert not backup_service.verify_file(env / "missing.db").ok

    def test_compatibility_with_this_version(self):
        from app.core import schema_state

        assert backup_service.compatibility_of(schema_state.code_head()) == "current"
        assert backup_service.compatibility_of("0007") == "upgradable"
        assert (
            backup_service.compatibility_of("9999") == "incompatible"
            and backup_service.compatibility_of(None) == "incompatible"
        )


def record(key, days_ago, status=BackupStatus.VERIFIED, now=None):
    now = now or utc_now()
    r = BackupRecord(
        backup_key=key,
        kind=BackupKind.SCHEDULED,
        status=status,
        storage_provider="local",
        initiated_by="t",
        filename=f"{key}.db",
    )
    r.created_at = now - timedelta(days=days_ago)
    return r


class TestRetention:
    S = Settings(_env_file=None, backup_keep_daily_days=7, backup_keep_weekly_weeks=3)

    def test_recent_backups_are_kept_old_ones_thinned_to_one_a_week(self):
        # Pinned: the weekly window is 21 days of time, which touches 3 or 4 calendar weeks depending on the weekday
        # the test happens to run on. A Monday makes it exactly 3, so the test no longer depends on today's date.
        now = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        rs = [record(f"d{i}", i, now=now) for i in range(0, 30)]
        keep, delete = backup_service.plan_retention(rs, now, self.S)
        kept = {r.backup_key for r in keep}
        assert {f"d{i}" for i in range(0, 8)} <= kept  # the last week is all kept
        weeks = {
            (r.created_at.isocalendar()[0], r.created_at.isocalendar()[1])
            for r in keep
            if r.created_at < now - timedelta(days=7)
        }
        assert 1 <= len(weeks) <= 3 and len(
            [r for r in keep if r.created_at < now - timedelta(days=7)]
        ) == len(weeks)
        assert (
            delete
            and {r.backup_key for r in delete}.isdisjoint(kept)
            and "d29" in {r.backup_key for r in delete}
        )

    def test_the_only_valid_backup_is_never_deleted_however_old(self):
        now = utc_now()
        keep, delete = backup_service.plan_retention([record("only", 400, now=now)], now, self.S)
        assert [r.backup_key for r in keep] == ["only"] and delete == []
        keep, delete = backup_service.plan_retention(
            [record("bad", 1, BackupStatus.FAILED, now), record("old", 400, now=now)], now, self.S
        )
        assert [r.backup_key for r in keep] == [
            "old"
        ] and delete == []  # a failed backup does not count as a valid one

    def test_the_newest_is_always_kept(self):
        now = utc_now()
        keep, _ = backup_service.plan_retention(
            [record("a", 100, now=now), record("b", 90, now=now)], now, self.S
        )
        assert "b" in {r.backup_key for r in keep}

    def test_applying_is_a_dry_run_by_default_deletes_files_and_keeps_the_record(
        self, env, stocked, session_factory, monkeypatch
    ):
        monkeypatch.setenv("KIRANA_BACKUP_KEEP_DAILY_DAYS", "1")
        monkeypatch.setenv("KIRANA_BACKUP_KEEP_WEEKLY_WEEKS", "0")
        get_settings.cache_clear()
        keys = [make(session_factory)[1] for _ in range(3)]
        with session_factory() as s, s.begin():  # age two of them
            for key, days in ((keys[0], 60), (keys[1], 40)):
                backup_service.get_record(s, key).created_at = utc_now() - timedelta(days=days)
        with session_factory() as s, s.begin():
            plan = backup_service.apply_retention(s, actor="t")
        assert (
            plan["delete"] and plan["deleted"] == [] and len(list(env.glob("*.db"))) == 3
        )  # a dry run removed nothing
        with session_factory() as s, s.begin():
            plan = backup_service.apply_retention(s, actor="t", dry_run=False)
        assert set(plan["deleted"]) == set(keys[:2]) and len(list(env.glob("*.db"))) == 1
        with session_factory() as s:
            assert (
                backup_service.get_record(s, keys[0]).status is BackupStatus.DELETED
                and backup_service.get_record(s, keys[0]).deleted_at
            )
            assert backup_service.get_record(s, keys[2]).status is BackupStatus.VERIFIED and not list(
                env.glob(f"{keys[0]}*")
            )


class TestRestore:
    def rec(self, session_factory, key):
        with session_factory() as s:
            return backup_service.get_record(s, key)

    def test_a_rehearsal_checks_a_copy_and_leaves_the_live_database_alone(
        self, env, stocked, session_factory, db_url
    ):
        _, key = make(session_factory)
        before = (
            sqlite3.connect(db_url.removeprefix("sqlite:///"))
            .execute("SELECT count(*) FROM products")
            .fetchone()
        )
        result = restore_service.rehearse(self.rec(session_factory, key), "tester")
        assert result.ok and result.report["products"] == 1 and result.verification.compatibility == "current"
        assert (
            sqlite3.connect(db_url.removeprefix("sqlite:///"))
            .execute("SELECT count(*) FROM products")
            .fetchone()
            == before
        )
        log = (env / "restore_log.jsonl").read_text().strip().splitlines()
        assert json.loads(log[-1])["mode"] == "REHEARSAL" and json.loads(log[-1])["initiated_by"] == "tester"

    def test_an_older_backup_is_rehearsed_with_its_migrations_on_the_copy(
        self, env, stocked, session_factory, tmp_path
    ):
        from alembic import command

        from tests.conftest import alembic_config

        old = tmp_path / "old.db"
        command.upgrade(alembic_config(f"sqlite:///{old}"), "0011")
        sha = backup_service._sha256(old)
        (env).mkdir(parents=True, exist_ok=True)
        (env / "bkp_old.db").write_bytes(old.read_bytes())
        record = BackupRecord(
            backup_key="bkp_old",
            kind=BackupKind.MANUAL,
            status=BackupStatus.VERIFIED,
            storage_provider="local",
            initiated_by="t",
            filename="bkp_old.db",
            sha256=sha,
            schema_revision="0011",
        )
        result = restore_service.rehearse(record, "tester")
        assert result.ok and "migrations" in result.detail and result.verification.compatibility == "current"
        assert (
            sqlite3.connect(old).execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0011"
        )  # the original file is untouched

    def test_an_invalid_backup_cannot_be_restored_or_rehearsed(self, env, stocked, session_factory, db_url):
        _, key = make(session_factory)
        (env / f"{key}.db").write_bytes(b"garbage")
        record = self.rec(session_factory, key)
        assert not restore_service.rehearse(record, "t").ok and not restore_service.validate(record, "t").ok
        result = restore_service.restore(
            record, confirmation=restore_service.confirmation_phrase(key), actor="t", via_api=False
        )
        assert not result.ok and "did not pass" in result.detail
        assert sqlite3.connect(db_url.removeprefix("sqlite:///")).execute(
            "SELECT count(*) FROM products"
        ).fetchone() == (1,)

    def test_restore_needs_the_exact_phrase_and_is_off_for_the_api_by_default(
        self, env, stocked, session_factory
    ):
        _, key = make(session_factory)
        record = self.rec(session_factory, key)
        bad = restore_service.restore(record, confirmation="yes please", actor="t", via_api=False)
        assert not bad.ok and "confirmation did not match" in bad.detail
        api = restore_service.restore(
            record, confirmation=restore_service.confirmation_phrase(key), actor="t", via_api=True
        )
        assert not api.ok and "turned off" in api.detail

    def test_a_restore_replaces_the_data_after_a_safety_backup_and_is_logged(
        self, env, stocked, session_factory, db_url, tenant_a, units, engine, session, monkeypatch
    ):
        _, key = make(session_factory)  # one product
        make_product(stocked, tenant_a, units, "BK2")  # a second product, made AFTER the backup
        live = db_url.removeprefix("sqlite:///")
        assert sqlite3.connect(live).execute("SELECT count(*) FROM products").fetchone() == (2,)
        record = self.rec(session_factory, key)
        session.close()  # the fixture's session has an open read transaction; a restore runs with everything closed
        gc.collect()  # the assertions above left a temporary connection for the collector
        engine.dispose()  # a restore is done with the application stopped: no pooled connections hold the database
        outcomes = []
        result = restore_service.restore(record, confirmation=restore_service.confirmation_phrase(key), actor="ops@test",
                                         via_api=False, pre_restore_recorder=outcomes.append)  # fmt: skip
        assert (
            result.ok
            and result.pre_restore_key
            and outcomes[0].ok
            and outcomes[0].kind is BackupKind.PRE_RESTORE
        )
        assert sqlite3.connect(live).execute("SELECT count(*) FROM products").fetchone() == (
            1,
        )  # back to the backup's state
        safety = (
            sqlite3.connect(env / outcomes[0].filename).execute("SELECT count(*) FROM products").fetchone()
        )
        assert safety == (2,)  # the state just before the restore is kept
        restore_service.note_in_database(result, "ops@test", live_path := __import__("pathlib").Path(live))
        row = (
            sqlite3.connect(live_path)
            .execute("SELECT backup_key, mode, status, initiated_by FROM restore_records")
            .fetchone()
        )
        assert row == (key, "RESTORE", "OK", "ops@test")
        entry = json.loads((env / "restore_log.jsonl").read_text().strip().splitlines()[-1])
        assert (
            entry["mode"] == "RESTORE"
            and entry["ok"]
            and entry["initiated_by"] == "ops@test"
            and entry["pre_restore_key"] == result.pre_restore_key
        )

    def test_nothing_changes_when_the_safety_backup_cannot_be_made(
        self, env, stocked, session_factory, db_url, tenant_a, units, monkeypatch
    ):
        _, key = make(session_factory)
        make_product(stocked, tenant_a, units, "BK2")
        monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", str(10**9))
        get_settings.cache_clear()
        result = restore_service.restore(
            self.rec(session_factory, key),
            confirmation=restore_service.confirmation_phrase(key),
            actor="t",
            via_api=False,
        )
        assert not result.ok and "safety backup" in result.detail
        assert sqlite3.connect(db_url.removeprefix("sqlite:///")).execute(
            "SELECT count(*) FROM products"
        ).fetchone() == (2,)

    def test_an_incompatible_backup_is_refused(self, env, stocked, session_factory, db_url):
        _, key = make(session_factory)
        path = env / f"{key}.db"
        with sqlite3.connect(path) as db:
            db.execute("UPDATE alembic_version SET version_num = '9999'")
        sha = backup_service._sha256(path)
        record = self.rec(session_factory, key)
        record.sha256 = sha
        result = restore_service.restore(
            record, confirmation=restore_service.confirmation_phrase(key), actor="t", via_api=False
        )
        assert not result.ok and "not compatible" in result.detail

    def test_a_database_in_use_is_not_overwritten(
        self, env, stocked, session_factory, db_url, tenant_a, units
    ):
        _, key = make(session_factory)
        make_product(stocked, tenant_a, units, "BK2")
        holder = sqlite3.connect(db_url.removeprefix("sqlite:///"), isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")  # another connection holds the database
        try:
            result = restore_service.restore(
                self.rec(session_factory, key),
                confirmation=restore_service.confirmation_phrase(key),
                actor="t",
                via_api=False,
            )
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert not result.ok and "in use" in result.detail
        assert sqlite3.connect(db_url.removeprefix("sqlite:///")).execute(
            "SELECT count(*) FROM products"
        ).fetchone() == (2,)


class TestAdminApi:
    @pytest.fixture
    def tokens(self, session_factory):
        out = {}
        for role in AdminRole:
            with session_factory() as s, s.begin():
                out[role] = admin_service.create_admin(
                    s, email=f"{role.value}@ops.test".lower(), display_name="x", role=role
                )[1]
        return out

    def call(self, client, method, path, token, **kw):
        return client.request(method, f"{ADMIN}{path}", headers={"X-Admin-Token": token}, **kw)

    def test_create_list_verify_rehearse_and_retention_through_the_api(self, env, stocked, tokens):
        ops = tokens[AdminRole.OPERATIONS_ADMIN]
        made = self.call(stocked, "POST", "/backups", ops)
        assert (
            made.status_code == 201
            and made.json()["status"] == "VERIFIED"
            and made.json()["initiated_by"] == "operations_admin@ops.test"
        )
        key = made.json()["backup_key"]
        assert "filename" not in made.json() and "path" not in str(made.json()).lower()
        assert self.call(stocked, "GET", "/backups", ops).json()[0]["backup_key"] == key
        assert self.call(stocked, "POST", f"/backups/{key}/verify", ops).json()["ok"] is True
        rehearsal = self.call(stocked, "POST", f"/backups/{key}/rehearse-restore", ops).json()
        assert (
            rehearsal["ok"]
            and rehearsal["mode"] == "REHEARSAL"
            and rehearsal["confirmation_phrase"] == f"RESTORE {key}"
        )
        plan = self.call(stocked, "POST", "/backups/retention", ops, json={}).json()
        assert plan["dry_run"] is True and plan["deleted"] == []
        assert self.call(stocked, "POST", "/backups/bkp_nope/verify", ops).status_code == 404

    def test_the_restore_endpoint_is_super_admin_only_and_off_by_default(self, env, stocked, tokens):
        key = self.call(stocked, "POST", "/backups", tokens[AdminRole.OPERATIONS_ADMIN]).json()["backup_key"]
        body = {"confirmation": f"RESTORE {key}"}
        assert (
            self.call(
                stocked, "POST", f"/backups/{key}/restore", tokens[AdminRole.OPERATIONS_ADMIN], json=body
            ).status_code
            == 403
        )
        off = self.call(
            stocked, "POST", f"/backups/{key}/restore", tokens[AdminRole.SUPER_ADMIN], json=body
        ).json()
        assert off["ok"] is False and "turned off" in off["detail"]

    def test_a_failed_backup_via_the_api_is_reported_not_raised(self, env, stocked, tokens, monkeypatch):
        monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", str(10**9))
        get_settings.cache_clear()
        r = self.call(stocked, "POST", "/backups", tokens[AdminRole.SUPER_ADMIN])
        assert (
            r.status_code == 201 and r.json()["status"] == "FAILED" and r.json()["error_code"] == "disk_space"
        )

    def test_the_owner_sees_only_a_calm_backup_status(self, env, stocked, session_factory):
        assert stocked.get("/api/v1/account/backup-status").json() == {
            "state": "none",
            "last_backup_at": None,
        }
        make(session_factory)
        body = stocked.get("/api/v1/account/backup-status").json()
        assert body["state"] == "recent" and set(body) == {"state", "last_backup_at"}


class TestCommandLine:
    """The operator tool, run as the operator would run it (found a detached-record bug and a self-held database)."""

    @pytest.fixture
    def cli(self, env, stocked, engine, session_factory, monkeypatch, capsys):
        from app import backup_cli
        from app.db import session as session_module

        monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
        monkeypatch.setattr(backup_cli, "get_engine", lambda: engine)

        def run(*args):
            code = backup_cli.main(list(args))
            return code, capsys.readouterr().out

        return run

    def test_create_list_verify_rehearse_and_retention(self, cli):
        code, out = cli("create")
        assert code == 0 and out.startswith("OK bkp_")
        key = out.split()[1]
        assert key in cli("list")[1]
        assert cli("verify", key)[0] == 0
        code, out = cli("rehearse", key)
        assert code == 0 and "restores cleanly" in out
        code, out = cli("retention")
        assert code == 0 and out.startswith("Would delete")

    def test_an_unknown_backup_is_reported_not_a_traceback(self, cli):
        with pytest.raises(SystemExit) as info:
            cli("rehearse", "bkp_nope")
        assert "No backup named" in str(info.value)

    def test_restore_needs_the_phrase_and_then_really_restores(
        self, cli, db_url, session, tenant_a, units, stocked
    ):
        def products() -> int:
            connection = sqlite3.connect(db_url.removeprefix("sqlite:///"))
            try:
                return connection.execute("SELECT count(*) FROM products").fetchone()[0]
            finally:
                connection.close()  # a restore needs the database to itself: no stray connection may hold it

        key = cli("create")[1].split()[1]
        make_product(stocked, tenant_a, units, "BK2")
        session.close()
        assert products() == 2
        code, out = cli("restore", key, "--confirm", "yes")
        assert code == 1 and "did not match" in out
        assert products() == 2
        code, out = cli("restore", key, "--confirm", f"RESTORE {key}")
        assert code == 0 and "restored" in out
        assert products() == 1
