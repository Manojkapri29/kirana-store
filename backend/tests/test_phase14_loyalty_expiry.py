"""Loyalty expiry: old unused points expire through an EXPIRE ledger row, never more than the balance, never
twice for the same day, and not at all when the program has no expiry configured."""

from datetime import timedelta
from decimal import Decimal

from app.models.enums import LoyaltyEntryType
from app.services import loyalty_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _program(session, ctx, expiry_days):
    loyalty_service.configure_program(
        session, ctx, is_active=True, points_per_amount=Decimal("0.01"), redemption_value=Decimal("1"),
        points_expiry_days=expiry_days,
    )  # fmt: skip
    session.commit()


def _earn(session, ctx, customer, points, days_ago, ref):
    loyalty_service.grant_reward(
        session, ctx, customer_id=customer.id, points=points, reference_type="TEST", reference_id=ref,
        entry_date=TODAY - timedelta(days=days_ago), note="seed",
    )  # fmt: skip
    session.commit()


def test_nothing_expires_when_the_program_has_no_expiry(session, tenant_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, None)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 100, 900, 1)

    result = loyalty_service.expire_points(session, ctx, TODAY)

    assert (result.customers_expired, result.points_expired) == (0, 0)
    assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 100


def test_old_points_expire_and_recent_points_survive(session, tenant_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, 365)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 100, 400, 1)  # past the window
    _earn(session, ctx, customer, 30, 10, 2)  # recent

    result = loyalty_service.expire_points(session, ctx, TODAY)
    session.commit()

    assert (result.customers_expired, result.points_expired) == (1, 100)
    assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 30
    entries, _ = loyalty_service.list_ledger(session, tenant_a.shop.id, customer.id)
    assert LoyaltyEntryType.EXPIRE in {e.entry_type for e in entries}


def test_points_already_redeemed_do_not_expire_twice(session, tenant_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, 365)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 100, 400, 1)
    loyalty_service.record_redeem(session, ctx, customer_id=customer.id, points=60, entry_date=TODAY)
    session.commit()

    result = loyalty_service.expire_points(session, ctx, TODAY)
    session.commit()

    assert result.points_expired == 40  # only what is left of the old earnings
    assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 0


def test_running_twice_the_same_day_expires_nothing_more(session, tenant_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, 365)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 100, 400, 1)
    loyalty_service.expire_points(session, ctx, TODAY)
    session.commit()

    again = loyalty_service.expire_points(session, ctx, TODAY)

    assert again.points_expired == 0


def test_the_balance_never_goes_negative(session, tenant_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, 365)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 100, 400, 1)
    loyalty_service.record_adjust(
        session, ctx, customer_id=customer.id, points_delta=-70, entry_date=TODAY, note="Correction"
    )
    session.commit()

    loyalty_service.expire_points(session, ctx, TODAY)
    session.commit()

    assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 0


def test_the_expire_endpoint_reports_the_result(session, tenant_a, client_a):
    ctx = context_for(tenant_a)
    _program(session, ctx, 365)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    _earn(session, ctx, customer, 50, 400, 1)

    body = client_a.post("/api/v1/loyalty/expire").json()

    assert body == {"customers_expired": 1, "points_expired": 50}
