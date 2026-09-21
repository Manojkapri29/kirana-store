"""Development seed data: one shop, one owner, nothing that looks like real business data."""

import pytest
from sqlalchemy import func, select, text

from app import seed
from app.core.config import Settings
from app.db import session as session_module
from app.models import Base, Shop, User
from app.models.enums import UserRole
from app.seed import DEV_SHOP_NAME, DEV_USER_EMAIL, UNUSABLE_PASSWORD_HASH, seed_development_data

# Tables that are allowed to have rows after seeding.
SEEDED_TABLES = {"shops", "users", "units", "business_types", "plans", "plan_features", "shop_subscriptions"}


def count(session, table: str) -> int:
    return session.scalar(text(f"SELECT count(*) FROM {table}"))


def test_seed_creates_one_development_shop_and_owner(session):
    result = seed_development_data(session)
    session.commit()

    assert result.created is True
    shop = session.scalars(select(Shop)).one()
    user = session.scalars(select(User)).one()
    assert shop.name == DEV_SHOP_NAME
    assert user.email == DEV_USER_EMAIL
    assert user.role is UserRole.OWNER
    assert user.shop_id == shop.id
    assert (result.shop_id, result.user_id) == (shop.id, user.id)


def test_seeding_twice_changes_nothing(session):
    first = seed_development_data(session)
    session.commit()

    second = seed_development_data(session)
    session.commit()

    assert second.created is False
    assert (second.shop_id, second.user_id) == (first.shop_id, first.user_id)
    assert count(session, "shops") == 1
    assert count(session, "users") == 1


def test_the_development_user_cannot_log_in(session):
    seed_development_data(session)
    session.commit()

    user = session.scalars(select(User)).one()

    assert user.password_hash == UNUSABLE_PASSWORD_HASH == "!"
    assert "$" not in user.password_hash  # not the shape of any real password hash


def test_no_products_sales_or_money_are_seeded(session):
    seed_development_data(session)
    session.commit()

    populated = {name for name in Base.metadata.tables if count(session, name) > 0}

    assert populated == SEEDED_TABLES
    assert session.scalar(select(func.count()).select_from(Shop)) == 1


def test_units_are_reference_data_present_before_any_seeding(session):
    assert count(session, "units") == 12
    assert count(session, "shops") == 0


def test_seed_command_refuses_to_run_in_production():
    with pytest.raises(SystemExit, match="production"):
        seed.main(Settings(environment="production", _env_file=None))


def test_seed_command_runs_in_its_own_transaction(monkeypatch, session_factory, capsys):
    monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)

    seed.main(Settings(environment="development", _env_file=None))
    seed.main(Settings(environment="development", _env_file=None))

    output = capsys.readouterr().out
    assert "created" in output and "already present" in output
    with session_factory() as session:
        assert count(session, "shops") == 1
