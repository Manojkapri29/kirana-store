"""Migration 0014 (operations, account lifecycle, notifications): a fresh database and an existing Phase 10 database."""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.engine import create_db_engine
from tests.conftest import alembic_config, sqlite_url
from tests.test_phase9_migrations import NOW, seed_at_0010

NEW_TABLES = {
    "system_admins", "admin_audit_logs", "support_access_grants", "system_events", "backup_records", "restore_records",
    "notification_events", "notification_deliveries", "notification_preferences",
}  # fmt: skip


def rows(url, sql):
    engine = create_db_engine(url)
    with engine.connect() as c:
        out = [tuple(r) for r in c.execute(text(sql))]
    engine.dispose()
    return out


def tables(url):
    return {r[0] for r in rows(url, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_phase_10_database_is_upgraded_with_its_data_intact(tmp_path):
    url = sqlite_url(tmp_path / "m.db")
    seed_at_0010(url)
    command.upgrade(alembic_config(url), "0013")
    before_sales = rows(url, "SELECT id, invoice_no, total_amount, status FROM sales")
    before_plan = rows(
        url,
        "SELECT p.code, f.feature_key, f.enabled, f.limit_value FROM plan_features f JOIN plans p ON p.id = f.plan_id ORDER BY 1, 2",
    )
    command.upgrade(alembic_config(url), "0014")

    assert NEW_TABLES <= tables(url)
    assert rows(url, "SELECT id, invoice_no, total_amount, status FROM sales") == before_sales
    assert rows(url, "SELECT id, account_status, status_reason FROM shops") == [
        (7, "ACTIVE", None)
    ]  # every shop stays active
    after_plan = rows(
        url,
        "SELECT p.code, f.feature_key, f.enabled, f.limit_value FROM plan_features f JOIN plans p ON p.id = f.plan_id ORDER BY 1, 2",
    )
    assert [
        r
        for r in after_plan
        if r[1] not in ("exports", "max_exports_per_month", "max_image_analyses_per_month")
    ] == before_plan
    entries = {
        (c, k): (bool(e), lim)
        for c, k, e, lim in after_plan
        if k in ("exports", "max_exports_per_month", "max_image_analyses_per_month")
    }
    assert all(
        entries[(plan, "exports")] == (True, None) for plan in ("free", "basic", "pro")
    )  # exports stay available to every plan
    assert all(
        entries[(plan, "max_exports_per_month")][1] is None for plan in ("free", "basic", "pro")
    )  # and unlimited: no invented policy
    assert rows(url, "PRAGMA foreign_key_check") == []
    command.check(alembic_config(url))


def test_a_fresh_database_reaches_head_and_the_code_agrees(tmp_path):
    from app.core import schema_state

    url = sqlite_url(tmp_path / "fresh.db")
    command.upgrade(alembic_config(url), "head")
    assert rows(url, "SELECT version_num FROM alembic_version") == [(schema_state.code_head(),)]
    command.check(alembic_config(url))


def test_downgrade_removes_only_what_0014_added(tmp_path):
    url = sqlite_url(tmp_path / "m.db")
    seed_at_0010(url)
    command.upgrade(alembic_config(url), "0013")
    before = rows(url, "SELECT id, invoice_no, total_amount FROM sales")
    plan_before = rows(url, "SELECT count(*) FROM plan_features")
    command.upgrade(alembic_config(url), "0014")
    command.downgrade(alembic_config(url), "0013")
    assert not NEW_TABLES & tables(url)
    assert rows(url, "SELECT id, invoice_no, total_amount FROM sales") == before
    assert rows(url, "SELECT count(*) FROM plan_features") == plan_before
    assert "account_status" not in {r[1] for r in rows(url, "PRAGMA table_info(shops)")}
    command.upgrade(alembic_config(url), "head")
    command.check(alembic_config(url))


def test_the_admin_audit_log_cannot_be_changed_or_deleted(tmp_path):
    url = sqlite_url(tmp_path / "m.db")
    command.upgrade(alembic_config(url), "head")
    engine = create_db_engine(url)
    with engine.begin() as c:
        c.execute(
            text(f"INSERT INTO admin_audit_logs (action, outcome, created_at) VALUES ('test', 'OK', {NOW})")
        )
    for statement in ("UPDATE admin_audit_logs SET action = 'x'", "DELETE FROM admin_audit_logs"):
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(statement))
    engine.dispose()


def test_notification_rows_reject_unknown_channels_states_and_another_shops_events(tmp_path):
    url = sqlite_url(tmp_path / "m.db")
    seed_at_0010(url)
    command.upgrade(alembic_config(url), "head")
    engine = create_db_engine(url)
    with engine.begin() as c:
        c.execute(
            text(
                f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES (8, 'T', 'BAKERY', '9', 'x', 'WARN', {NOW}, {NOW})"
            )
        )
        c.execute(
            text(
                f"INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (4, 8, 'p@x.l', '!', 'P', 'OWNER', 1, {NOW}, {NOW})"
            )
        )
        c.execute(
            text(
                f"INSERT INTO notification_events (id, shop_id, event_type, category, dedupe_key, title, message, created_at) VALUES (1, 7, 'LOW_STOCK', 'inventory', 'k', 't', 'm', {NOW})"
            )
        )
    insert = f"INSERT INTO notification_deliveries (shop_id, event_id, user_id, channel, status, attempts, updated_at, created_at) VALUES (:shop, 1, :user, :channel, :status, 0, {NOW}, {NOW})"
    good = {"shop": 7, "user": 3, "channel": "IN_APP", "status": "SENT"}
    with engine.begin() as c:
        c.execute(text(insert), good)
    for bad in (
        {"channel": "CARRIER_PIGEON"},
        {"status": "DELIVERED"},
        {"shop": 8, "user": 4},
    ):  # unknown channel, unknown state, shop 8 using shop 7's event
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(
                text(insert), {**good, "user": 3, **bad, **({"channel": "EMAIL"} if "status" in bad else {})}
            )
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            text(insert), good
        )  # the same person is never notified twice on the same channel about the same event
    engine.dispose()
