"""Phase 12 operations: metrics, background jobs, production configuration, query counts and migration 0015."""

import json
import sqlite3
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import event, text

from app.core import metrics
from app.core.config import Settings, get_settings
from app.db import session as session_module
from app.db.engine import create_db_engine
from app.db.types import utc_now
from app.models.enums import JobStatus
from app.services import background_job_service as jobs
from tests import factories
from tests.conftest import alembic_config, sqlite_url
from tests.test_phase9_migrations import NOW, seed_at_0010

API = "/api/v1"
KEY = "k" * 40


@pytest.fixture
def patched(monkeypatch, session_factory):
    monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
    return session_factory


class TestMetrics:
    @pytest.fixture
    def scrape(self, monkeypatch, real_client):
        monkeypatch.setenv("KIRANA_METRICS_ENABLED", "true")
        monkeypatch.setenv("KIRANA_METRICS_TOKEN", "m" * 24)
        get_settings.cache_clear()
        metrics.registry.reset()
        yield lambda **kw: real_client().get("/metrics", **kw)
        monkeypatch.undo()
        get_settings.cache_clear()

    def test_the_endpoint_does_not_exist_unless_switched_on(self, real_client):
        assert real_client().get("/metrics").status_code == 404

    def test_it_needs_the_token(self, scrape):
        assert scrape().status_code == 401
        assert scrape(headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert scrape(headers={"Authorization": "Bearer " + "m" * 24}).status_code == 200

    def test_it_counts_requests_by_route_template_and_carries_no_personal_data(
        self, scrape, real_client, owner_login, sign_in, session_factory
    ):
        client = sign_in(owner_login)
        client.get(f"{API}/products")
        client.get(f"{API}/customers/12345")
        client.get("/no/such/place/abc")
        token = client.cookies.get("kirana_session")
        body = scrape(headers={"Authorization": "Bearer " + "m" * 24}).text
        assert 'kirana_http_requests_total{method="GET",route="/api/v1/products",status="2xx"} 1' in body
        assert 'route="/api/v1/customers/{id}"' in body and "12345" not in body  # record numbers are masked
        assert 'route="other"' in body and "abc" not in body  # unknown paths cannot grow the label set
        assert "kirana_http_request_duration_seconds_bucket" in body and "kirana_auth_events_total" in body
        assert owner_login not in body and token not in body and "m" * 24 not in body

    def test_errors_and_platform_events_are_counted(self, scrape, real_client, session_factory):
        from app.models.enums import EventSeverity
        from app.services import system_event_service

        real_client().post(f"{API}/auth/login", json={"email": "x@example.com", "password": "not the one"})
        with session_factory() as s, s.begin():
            system_event_service.record(
                s, category="ai", severity=EventSeverity.ERROR, source="t", code="c", message="m"
            )
            system_event_service.record(
                s, category="integration", severity=EventSeverity.WARNING, source="t", code="c", message="m"
            )
            system_event_service.record(
                s, category="notification", severity=EventSeverity.WARNING, source="t", code="c", message="m"
            )
        assert metrics.registry.value("kirana_auth_events_total", event="login_failed") == 1
        assert metrics.registry.total("kirana_ai_failures_total") == 1
        assert metrics.registry.total("kirana_external_api_errors_total") == 1
        assert metrics.registry.total("kirana_notification_failures_total") == 1
        assert (
            metrics.registry.value(
                "kirana_http_requests_total", method="POST", route="/api/v1/auth/login", status="4xx"
            )
            == 1
        )

    def test_metrics_in_production_need_a_token(self):
        problems = Settings(
            _env_file=None, environment="production", metrics_enabled=True
        ).production_problems()
        assert any("METRICS_TOKEN" in p for p in problems)


class TestJobs:
    @pytest.fixture(autouse=True)
    def _register(self):
        calls = {"n": 0, "fail_until": 0}

        @jobs.register("test.flaky")
        def flaky(session, payload):
            calls["n"] += 1
            if calls["n"] <= calls["fail_until"]:
                raise RuntimeError("secret detail: password=hunter2 at /Users/x/db.py")
            return {"ok": True, "echo": payload.get("value")}

        self.calls = calls
        yield
        jobs.HANDLERS.pop("test.flaky", None)

    def make(self, patched, **kw):
        with patched() as s, s.begin():
            return jobs.enqueue(s, "test.flaky", {"value": 7}, **kw).id

    def state(self, patched, job_id):
        with patched() as s:
            j = s.get(jobs.BackgroundJob, job_id)
            return j.status, j.attempts, j.error_code, j.error_message, j.result, j.started_at, j.completed_at

    def test_a_job_runs_once_and_completes_with_a_result(self, patched):
        job_id = self.make(patched)
        outcomes = jobs.run_due()
        assert [o["status"] for o in outcomes] == ["COMPLETED"]
        status, attempts, code, message, result, started, completed = self.state(patched, job_id)
        assert (status, attempts, code, message, result) == (
            JobStatus.COMPLETED,
            1,
            None,
            None,
            {"ok": True, "echo": 7},
        )
        assert (
            started and completed and jobs.run_due() == []
        )  # nothing left to run, and it does not run again

    def test_the_same_idempotency_key_is_queued_once(self, patched):
        a = self.make(patched, idempotency_key="k1")
        b = self.make(patched, idempotency_key="k1")
        assert a == b
        assert self.make(patched, idempotency_key="k2") != a
        jobs.run_due()
        assert self.calls["n"] == 2

    def test_an_unknown_job_type_is_refused(self, patched):
        from app.services.errors import InvalidInputError

        with patched() as s, s.begin(), pytest.raises(InvalidInputError):
            jobs.enqueue(s, "no.such.job")

    def test_a_failure_is_retried_with_a_growing_delay_then_fails_safely(self, patched, monkeypatch):
        monkeypatch.setenv("KIRANA_JOB_BACKOFF_SECONDS", "60")
        get_settings.cache_clear()
        self.calls["fail_until"] = 99
        job_id = self.make(patched, max_attempts=3)
        try:
            assert jobs.run_due()[0]["status"] == "RETRYING"
            with patched() as s, s.begin():
                first = s.get(jobs.BackgroundJob, job_id).run_after
            assert jobs.run_due() == []  # not due yet: the delay is real
            with patched() as s, s.begin():
                s.get(jobs.BackgroundJob, job_id).run_after = utc_now() - timedelta(seconds=1)
            assert jobs.run_due()[0]["status"] == "RETRYING"
            with patched() as s, s.begin():
                second = s.get(jobs.BackgroundJob, job_id).run_after
                s.get(jobs.BackgroundJob, job_id).run_after = utc_now() - timedelta(seconds=1)
            assert (second - utc_now()).total_seconds() > (
                first - utc_now()
            ).total_seconds()  # 120s after the second failure, 60s after the first
            assert jobs.run_due()[0]["status"] == "FAILED"
            status, attempts, code, message, *_ = self.state(patched, job_id)
            assert status is JobStatus.FAILED and attempts == 3 and code == "handler_error"
            assert message == "The job could not be completed."  # nothing of the exception reaches the record
            assert "hunter2" not in json.dumps([code, message]) and "/Users" not in message
            with patched() as s:
                assert (
                    s.execute(text("SELECT count(*) FROM system_events WHERE code = 'job_failed'")).scalar()
                    == 1
                )
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_a_retry_that_succeeds_completes_once(self, patched):
        self.calls["fail_until"] = 1
        job_id = self.make(patched)
        assert jobs.run_due()[0]["status"] == "RETRYING"
        with patched() as s, s.begin():
            s.get(jobs.BackgroundJob, job_id).run_after = utc_now() - timedelta(seconds=1)
        assert jobs.run_due()[0]["status"] == "COMPLETED"
        assert self.state(patched, job_id)[:2] == (JobStatus.COMPLETED, 2)
        assert jobs.run_due() == []

    def test_a_cancelled_job_never_runs_and_a_finished_one_cannot_be_cancelled(self, patched):
        from app.services.errors import ConflictError

        job_id = self.make(patched)
        with patched() as s, s.begin():
            assert jobs.cancel(s, job_id).status is JobStatus.CANCELLED
        assert jobs.run_due() == [] and self.calls["n"] == 0
        done = self.make(patched)
        jobs.run_due()
        with patched() as s, s.begin(), pytest.raises(ConflictError):
            jobs.cancel(s, done)

    def test_a_job_whose_worker_died_is_picked_up_again(self, patched):
        job_id = self.make(patched)
        with patched() as s, s.begin():
            j = s.get(jobs.BackgroundJob, job_id)
            j.status, j.attempts, j.started_at = JobStatus.RUNNING, 1, utc_now() - timedelta(hours=1)
        assert jobs.run_due()[0]["status"] == "COMPLETED"
        assert self.state(patched, job_id)[1] == 2

    def test_the_handlers_work_is_all_or_nothing(self, patched):
        @jobs.register("test.halfway")
        def halfway(session, payload):
            session.execute(
                text(
                    "INSERT INTO shops (name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES ('Ghost','GROCERY','9','x','WARN',datetime('now'),datetime('now'))"
                )
            )
            raise RuntimeError("boom")

        try:
            with patched() as s, s.begin():
                jobs.enqueue(s, "test.halfway", max_attempts=1)
            assert jobs.run_due()[0]["status"] == "FAILED"
            with patched() as s:
                assert (
                    s.execute(text("SELECT count(*) FROM shops WHERE name = 'Ghost'")).scalar() == 0
                )  # rolled back
        finally:
            jobs.HANDLERS.pop("test.halfway", None)

    def test_the_routine_jobs_are_queued_once_per_day(self, patched):
        with patched() as s, s.begin():
            first = [j.id for j in jobs.schedule_periodic(s)]
        with patched() as s, s.begin():
            second = [j.id for j in jobs.schedule_periodic(s)]
        assert first == second and len(set(first)) == 4

    def test_the_notification_and_maintenance_jobs_run(self, patched):
        with patched() as s, s.begin():
            jobs.enqueue(s, "notifications.process_due")
            jobs.enqueue(s, "maintenance.purge_sessions")
        assert [o["status"] for o in jobs.run_due()] == ["COMPLETED", "COMPLETED"]

    def test_the_backup_job_makes_one_verified_backup(self, patched, tmp_path, monkeypatch, db_url):
        monkeypatch.setenv("KIRANA_DATABASE_URL", db_url)
        monkeypatch.setenv("KIRANA_BACKUP_DIR", str(tmp_path / "b"))
        monkeypatch.setenv("KIRANA_BACKUP_MIN_FREE_MB", "1")
        get_settings.cache_clear()
        try:
            with patched() as s, s.begin():
                jobs.enqueue(s, "backup.create", idempotency_key="daily:20260921")
                jobs.enqueue(s, "backup.create", idempotency_key="daily:20260921")  # asked twice, queued once
            assert [o["status"] for o in jobs.run_due()] == ["COMPLETED"]
            assert len(list((tmp_path / "b").glob("*.db"))) == 1
            with patched() as s:
                assert s.execute(text("SELECT status FROM backup_records")).scalar() == "VERIFIED"
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_the_worker_command_line(self, patched, capsys):
        from app import worker

        self.calls["fail_until"] = 99
        with patched() as s, s.begin():
            jobs.enqueue(s, "test.flaky", max_attempts=1)
        assert worker.main(["--once"]) == 1  # a permanent failure is a non-zero exit
        assert "FAILED" in capsys.readouterr().out
        assert worker.main(["--list"]) == 0 and "test.flaky" in capsys.readouterr().out

    def test_admins_can_see_recent_jobs_and_shops_cannot(
        self, patched, real_client, session_factory, owner_login, sign_in
    ):
        from app.models.enums import AdminRole
        from app.services import admin_service

        self.make(patched)
        with session_factory() as s, s.begin():
            token = admin_service.create_admin(
                s, email="ops@ops.test", display_name="Ops", role=AdminRole.OPERATIONS_ADMIN
            )[1]
        seen = real_client().get(f"{API}/admin/system/jobs", headers={"X-Admin-Token": token}).json()
        assert seen[0]["type"] == "test.flaky" and seen[0]["status"] == "PENDING"
        assert (
            sign_in(owner_login).get(f"{API}/admin/system/jobs").status_code == 401
        )  # a shop owner is not an administrator


class TestProductionConfiguration:
    PROD = dict(
        environment="production", secret_key=KEY, frontend_url="https://shop.example.com", cors_origins="https://shop.example.com",
        password_hash_time_cost=3, password_hash_memory_kib=65536, rate_limit_enabled=True,
    )  # fmt: skip

    def problems(self, **over):
        return Settings(_env_file=None, **{**self.PROD, **over}).production_problems()

    def test_a_safe_production_configuration_has_no_problems(self):
        assert self.problems() == []

    @pytest.mark.parametrize(
        ("over", "fragment"),
        [
            ({"dev_auth_bypass": True}, "DEV_AUTH_BYPASS"),
            ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE"),
            ({"password_hash_memory_kib": 1024}, "PASSWORD_HASH"),
            ({"password_hash_time_cost": 1}, "PASSWORD_HASH"),
            ({"secret_key": None}, "SECRET_KEY"),
            ({"frontend_url": "http://shop.example.com"}, "https"),
        ],
    )
    def test_unsafe_settings_are_named_never_their_values(self, over, fragment):
        found = self.problems(**over)
        assert any(fragment in p for p in found)
        assert KEY not in " ".join(found)

    def test_the_session_cookie_is_secure_by_default_only_in_production(self):
        assert Settings(_env_file=None, **self.PROD).cookie_secure is True
        assert Settings(_env_file=None).cookie_secure is False
        assert Settings(_env_file=None, **{**self.PROD, "session_cookie_secure": True}).cookie_secure is True

    def test_secrets_are_not_printed(self):
        s = Settings(_env_file=None, **self.PROD, metrics_token="t" * 20)
        assert KEY not in repr(s) and "t" * 20 not in repr(s) and KEY not in s.model_dump_json()

    def test_api_docs_are_off_in_production(self, monkeypatch):
        from app.main import create_app

        for k, v in {
            "KIRANA_ENVIRONMENT": "production",
            "KIRANA_SECRET_KEY": KEY,
            "KIRANA_FRONTEND_URL": "https://a.example.com",
            "KIRANA_CORS_ORIGINS": "https://a.example.com",
            "KIRANA_PASSWORD_HASH_TIME_COST": "3",
            "KIRANA_PASSWORD_HASH_MEMORY_KIB": "65536",
            "KIRANA_RATE_LIMIT_ENABLED": "true",
        }.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        try:
            app = create_app()
            assert app.openapi_url is None and app.docs_url is None
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_the_env_example_documents_the_phase_12_settings_with_placeholders_only(self):
        from pathlib import Path

        text_ = (Path(__file__).parent.parent / ".env.example").read_text()
        for name in (
            "KIRANA_SESSION_IDLE_MINUTES",
            "KIRANA_LOGIN_MAX_FAILURES",
            "KIRANA_TRUST_PROXY_HEADERS",
            "KIRANA_METRICS_ENABLED",
            "KIRANA_DEV_AUTH_BYPASS",
        ):
            assert name in text_, name
        assert "KIRANA_METRICS_TOKEN=" not in text_.replace(
            "# KIRANA_METRICS_TOKEN=", ""
        )  # never a live value


class TestPermissionChecksAreCheap:
    def count(self, engine, fn):
        seen = []

        @event.listens_for(engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return len(seen)

    def test_an_ordinary_request_costs_a_handful_of_queries(self, sign_in, owner_login, engine):
        client = sign_in(owner_login)
        client.get(f"{API}/products")  # warm up
        assert self.count(engine, lambda: client.get(f"{API}/products")) <= 12

    def test_the_staff_list_does_not_query_per_member(
        self, sign_in, owner_login, engine, session_factory, tenant_a
    ):
        client = sign_in(owner_login)
        few = self.count(engine, lambda: client.get(f"{API}/staff"))
        with session_factory() as s, s.begin():
            for i in range(25):
                factories.make_login(
                    s, tenant_a.shop, ["CASHIER", "MANAGER", "ACCOUNTANT"][i % 3], f"p{i}@test.local"
                )
        many = self.count(engine, lambda: client.get(f"{API}/staff"))
        assert many <= few + 2, (few, many)

    def test_the_audit_and_notification_and_role_lists_scale_too(
        self, sign_in, owner_login, engine, session_factory, tenant_a
    ):
        client = sign_in(owner_login)
        for path in ("/audit-log", "/notifications", "/roles", "/staff/invitations"):
            assert self.count(engine, lambda p=path: client.get(f"{API}{p}")) <= 14, path


def rows(url, sql):
    engine = create_db_engine(url)
    with engine.connect() as c:
        out = [tuple(r) for r in c.execute(text(sql))]
    engine.dispose()
    return out


class TestMigration0015:
    def test_a_phase_11_database_gets_accounts_memberships_and_roles_with_nothing_lost(self, tmp_path):
        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)  # a shop, an active OWNER user, a sale and a return
        command.upgrade(alembic_config(url), "0014")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (4, 7, 's@x.l', '!', 'Staff', 'STAFF', 0, {NOW}, {NOW})"
                )
            )
        engine.dispose()
        sales_before = rows(url, "SELECT id, invoice_no, total_amount FROM sales")
        command.upgrade(alembic_config(url), "head")
        assert rows(url, "SELECT id, invoice_no, total_amount FROM sales") == sales_before
        got = rows(
            url,
            "SELECT u.email, u.status, r.code, a.email, u.joined_at IS NOT NULL FROM users u JOIN roles r ON r.id = u.role_id JOIN accounts a ON a.id = u.account_id ORDER BY u.id",
        )
        assert got == [
            ("o@x.l", "ACTIVE", "OWNER", "o@x.l", 1),
            ("s@x.l", "SUSPENDED", "CASHIER", "s@x.l", 1),
        ]
        assert rows(url, "PRAGMA foreign_key_check") == []
        command.check(alembic_config(url))

    def test_the_six_system_roles_are_seeded_with_their_permissions(self, tmp_path):
        url = sqlite_url(tmp_path / "f.db")
        command.upgrade(alembic_config(url), "head")
        counts = dict(
            rows(
                url,
                "SELECT r.code, count(p.permission) FROM roles r JOIN role_permissions p ON p.role_id = r.id WHERE r.shop_id IS NULL GROUP BY r.code",
            )
        )
        assert (
            set(counts) == {"OWNER", "MANAGER", "CASHIER", "INVENTORY_STAFF", "SALES_STAFF", "ACCOUNTANT"}
            and counts["OWNER"] == 50
        )

    def test_one_email_can_now_belong_to_two_shops_but_not_twice_to_one(self, tmp_path):
        from sqlalchemy.exc import IntegrityError

        url = sqlite_url(tmp_path / "f.db")
        command.upgrade(alembic_config(url), "head")
        engine = create_db_engine(url)
        with engine.begin() as c:
            for i in (1, 2):
                c.execute(
                    text(
                        f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES ({i}, 'S{i}', 'BAKERY', '9', 'x', 'WARN', {NOW}, {NOW})"
                    )
                )
                c.execute(
                    text(
                        f"INSERT INTO users (shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ({i}, 'same@x.l', '!', 'P', 'STAFF', 1, {NOW}, {NOW})"
                    )
                )
            with pytest.raises(IntegrityError):
                c.execute(
                    text(
                        f"INSERT INTO users (shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (1, 'same@x.l', '!', 'P', 'STAFF', 1, {NOW}, {NOW})"
                    )
                )
        engine.dispose()

    def test_downgrade_works_and_is_refused_while_an_email_is_in_two_shops(self, tmp_path):
        url = sqlite_url(tmp_path / "d.db")
        command.upgrade(alembic_config(url), "head")
        command.downgrade(alembic_config(url), "0014")
        assert "accounts" not in {
            r[0] for r in rows(url, "SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        command.upgrade(alembic_config(url), "head")
        engine = create_db_engine(url)
        with engine.begin() as c:
            for i in (1, 2):
                c.execute(
                    text(
                        f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES ({i}, 'S{i}', 'BAKERY', '9', 'x', 'WARN', {NOW}, {NOW})"
                    )
                )
                c.execute(
                    text(
                        f"INSERT INTO users (shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ({i}, 'same@x.l', '!', 'P', 'STAFF', 1, {NOW}, {NOW})"
                    )
                )
        engine.dispose()
        with pytest.raises(RuntimeError, match="more than one shop"):
            command.downgrade(alembic_config(url), "0014")

    def test_the_session_and_invitation_tables_hold_no_plain_secret_columns(self, tmp_path):
        url = sqlite_url(tmp_path / "f.db")
        command.upgrade(alembic_config(url), "head")
        for table in ("auth_sessions", "invitations", "accounts"):
            names = {r[1] for r in rows(url, f"PRAGMA table_info({table})")}
            assert not names & {"token", "csrf_token", "password", "secret"}, (table, names)

    def test_seed_gives_the_development_owner_a_one_time_password(self, tmp_path, monkeypatch):
        from app import seed
        from app.db.session import write_transaction

        url = sqlite_url(tmp_path / "s.db")
        command.upgrade(alembic_config(url), "head")
        engine = create_db_engine(url)
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=engine, expire_on_commit=False)
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)
        with write_transaction() as s:
            first = seed.seed_development_data(s)
        with write_transaction() as s:
            second = seed.seed_development_data(s)
        assert (
            first.password and len(first.password) >= 12 and second.password is None
        )  # shown once, then never again
        with write_transaction() as s:
            from app.models import Account
            from app.services import password_service

            stored = s.query(Account).one().password_hash
        assert stored.startswith("$argon2id$") and password_service.verify_password(stored, first.password)
        engine.dispose()
        _ = sqlite3, factories


class TestAccountCommandLine:
    def run(self, capsys, *args):
        from app import account_cli

        code = account_cli.main(list(args))
        return code, capsys.readouterr().out

    def test_the_first_shop_and_owner_can_be_created_and_sign_in(self, patched, capsys, real_client):
        code, out = self.run(
            capsys,
            "create-shop",
            "--shop-name",
            "Sharma Store",
            "--owner-email",
            "Owner@Example.com",
            "--owner-name",
            "R Sharma",
        )
        password = out.split("(shown once): ")[1].split()[0]
        assert code == 0 and len(password) >= 12
        body = (
            real_client()
            .post(f"{API}/auth/login", json={"email": "owner@example.com", "password": password})
            .json()
        )
        assert (
            body["role_code"] == "OWNER"
            and body["shop_name"] == "Sharma Store"
            and "BACKUP_CREATE" in body["permissions"]
        )

    def test_set_password_ends_sessions_and_unlock_clears_a_pause(
        self, patched, capsys, real_client, sign_in, owner_login
    ):
        client = sign_in(owner_login)
        _, out = self.run(capsys, "set-password", "--email", owner_login)
        new = out.split("(shown once): ")[1].split()[0]
        assert client.get(f"{API}/products").status_code == 401
        assert (
            real_client()
            .post(f"{API}/auth/login", json={"email": owner_login, "password": factories.PASSWORD})
            .status_code
            == 401
        )
        assert (
            real_client().post(f"{API}/auth/login", json={"email": owner_login, "password": new}).status_code
            == 200
        )
        for _ in range(5):
            real_client().post(
                f"{API}/auth/login", json={"email": owner_login, "password": "wrong password here"}
            )
        assert (
            real_client().post(f"{API}/auth/login", json={"email": owner_login, "password": new}).status_code
            == 429
        )
        self.run(capsys, "unlock", "--email", owner_login)
        assert (
            real_client().post(f"{API}/auth/login", json={"email": owner_login, "password": new}).status_code
            == 200
        )

    def test_disable_ends_sessions_and_blocks_sign_in(
        self, patched, capsys, real_client, sign_in, owner_login
    ):
        client = sign_in(owner_login)
        self.run(capsys, "disable", "--email", owner_login)
        assert client.get(f"{API}/products").status_code == 401
        assert (
            real_client()
            .post(f"{API}/auth/login", json={"email": owner_login, "password": factories.PASSWORD})
            .status_code
            == 401
        )
        self.run(capsys, "enable", "--email", owner_login)
        assert (
            real_client()
            .post(f"{API}/auth/login", json={"email": owner_login, "password": factories.PASSWORD})
            .status_code
            == 200
        )

    def test_an_existing_email_is_not_overwritten_and_list_shows_no_secret(
        self, patched, capsys, owner_login
    ):
        with pytest.raises(SystemExit):
            self.run(
                capsys, "create-shop", "--shop-name", "Dup", "--owner-email", owner_login, "--owner-name", "X"
            )
        _, out = self.run(capsys, "list")
        assert owner_login in out and "argon2" not in out and factories.PASSWORD not in out
