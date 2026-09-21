"""Migration 0013 (AI usage, AI actions, AI plan entries): additive, reversible, and it changes no existing plan entry."""

from alembic import command
from sqlalchemy import text

from app.db.engine import create_db_engine
from tests.conftest import alembic_config, sqlite_url

AI_KEYS = ("ai_assistant", "ai_insights", "ai_documents", "max_ai_requests_per_month")


def rows(url, sql):
    engine = create_db_engine(url)
    with engine.connect() as c:
        out = [tuple(r) for r in c.execute(text(sql))]
    engine.dispose()
    return out


PLAN_ENTRIES = "SELECT p.code, f.feature_key, f.enabled, f.limit_value FROM plan_features f JOIN plans p ON p.id = f.plan_id ORDER BY 1, 2"


def test_upgrade_adds_the_tables_and_plan_entries_and_downgrade_removes_them(tmp_path):
    url = sqlite_url(tmp_path / "m.db")
    command.upgrade(alembic_config(url), "0012")
    before = rows(url, PLAN_ENTRIES)
    command.upgrade(alembic_config(url), "0013")

    tables = {r[0] for r in rows(url, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"ai_usage", "ai_actions"} <= tables
    after = rows(url, PLAN_ENTRIES)
    assert [r for r in after if r[1] not in AI_KEYS] == before  # nothing that existed was touched
    ai = {(code, key): (bool(enabled), limit) for code, key, enabled, limit in after if key in AI_KEYS}
    assert ai[("free", "ai_assistant")] == (True, None) and ai[("free", "ai_insights")] == (False, None)
    assert ai[("basic", "ai_insights")] == (True, None) and ai[("basic", "ai_documents")] == (False, None)
    assert ai[("pro", "ai_documents")] == (True, None)
    assert ai[("free", "max_ai_requests_per_month")] == (True, 30) and ai[
        ("pro", "max_ai_requests_per_month")
    ] == (True, None)

    command.downgrade(alembic_config(url), "0012")
    assert rows(url, PLAN_ENTRIES) == before
    assert not {"ai_usage", "ai_actions"} & {
        r[0] for r in rows(url, "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    command.upgrade(alembic_config(url), "head")
    command.check(alembic_config(url))


def test_an_action_cannot_carry_a_result_unless_it_was_executed(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from tests.test_phase9_migrations import NOW, seed_at_0010

    url = sqlite_url(tmp_path / "m.db")
    seed_at_0010(url)
    command.upgrade(alembic_config(url), "head")
    insert = (
        "INSERT INTO ai_actions (shop_id, created_by, kind, status, feature, proposal, current, attempts, result_type, "
        f"created_at, updated_at) VALUES (7, 3, 'PURCHASE_DRAFT', :status, 'assistant', '{{}}', '{{}}', 0, :result, {NOW}, {NOW})"
    )
    engine = create_db_engine(url)
    with engine.begin() as c:
        c.execute(text(insert), {"status": "EXECUTED", "result": "purchase"})
        c.execute(text(insert), {"status": "PROPOSED", "result": None})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text(insert), {"status": "PROPOSED", "result": "purchase"})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text(insert), {"status": "APPROVED_BY_AI", "result": None})  # not a real status
    engine.dispose()
