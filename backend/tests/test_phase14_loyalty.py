"""Loyalty: earning through a real sale, redemption, manual adjustment, reversal on void, and duplicate
prevention."""

from decimal import Decimal

import pytest

from app.services import inventory_service, loyalty_service
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _configure_program(session, ctx, **overrides):
    kwargs = {
        "is_active": True, "points_per_amount": Decimal("0.01"), "redemption_value": Decimal("1"),
    }  # fmt: skip
    kwargs.update(overrides)
    return loyalty_service.configure_program(session, ctx, **kwargs)


def _stock(session, ctx, product, qty):
    inventory_service.record_opening_stock(session, ctx, product_id=product.id, quantity=Decimal(qty))
    session.commit()


def _sell(client, product_id: int, quantity: int) -> int:
    resp = client.post(
        "/api/v1/sales", json={"items": [{"product_id": product_id, "quantity": str(quantity)}]}
    )
    assert resp.status_code == 201, resp.text
    sale_id = resp.json()["id"]
    resp = client.post(f"/api/v1/sales/{sale_id}/post", json={"payment_method": "CASH"})
    assert resp.status_code == 200, resp.text
    return sale_id


class TestProgramConfig:
    def test_every_rule_is_configurable_not_hardcoded(self, session, tenant_a):
        ctx = context_for(tenant_a)
        program = _configure_program(
            session, ctx, points_per_amount=Decimal("0.02"), min_redemption_points=50,
            max_redeem_points_per_txn=500, points_expiry_days=365,
        )  # fmt: skip
        session.commit()

        assert program.points_per_amount == Decimal("0.02")
        assert program.min_redemption_points == 50
        assert program.max_redeem_points_per_txn == 500
        assert program.points_expiry_days == 365

    def test_points_per_amount_must_be_positive(self, session, tenant_a):
        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            loyalty_service.configure_program(
                session, ctx, is_active=True, points_per_amount=Decimal("0"), redemption_value=Decimal("1")
            )


class TestEarningThroughASale:
    def test_posting_a_sale_earns_points_automatically(self, session, tenant_a, client_a):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx, points_per_amount=Decimal("1"))  # 1 point per rupee
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, selling_price=Decimal("100")
        )
        session.commit()
        _stock(session, ctx, product, 10)

        resp = client_a.post(
            "/api/v1/sales",
            json={"items": [{"product_id": product.id, "quantity": "2"}], "customer_id": customer.id},
        )
        assert resp.status_code == 201, resp.text
        sale_id = resp.json()["id"]
        resp = client_a.post(f"/api/v1/sales/{sale_id}/post", json={"payment_method": "CASH"})
        assert resp.status_code == 200, resp.text

        balance = loyalty_service.get_balance(session, tenant_a.shop.id, customer.id)
        assert balance == 200  # 200 rupees * 1 point/rupee

    def test_no_program_configured_earns_nothing_and_never_fails_the_sale(self, session, tenant_a, client_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, selling_price=Decimal("100")
        )
        session.commit()
        _stock(session, ctx, product, 10)

        resp = client_a.post(
            "/api/v1/sales",
            json={"items": [{"product_id": product.id, "quantity": "1"}], "customer_id": customer.id},
        )
        sale_id = resp.json()["id"]
        resp = client_a.post(f"/api/v1/sales/{sale_id}/post", json={"payment_method": "CASH"})

        assert resp.status_code == 200, resp.text
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 0

    def test_voiding_a_sale_reverses_its_points(self, session, tenant_a, client_a, fresh):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx, points_per_amount=Decimal("1"))
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, selling_price=Decimal("100")
        )
        session.commit()
        _stock(session, ctx, product, 10)

        resp = client_a.post(
            "/api/v1/sales",
            json={"items": [{"product_id": product.id, "quantity": "1"}], "customer_id": customer.id},
        )
        sale_id = resp.json()["id"]
        client_a.post(f"/api/v1/sales/{sale_id}/post", json={"payment_method": "CASH"})
        assert fresh(lambda s: loyalty_service.get_balance(s, tenant_a.shop.id, customer.id)) == 100

        resp = client_a.post(f"/api/v1/sales/{sale_id}/void", json={"reason": "Customer changed their mind"})
        assert resp.status_code == 200, resp.text

        assert fresh(lambda s: loyalty_service.get_balance(s, tenant_a.shop.id, customer.id)) == 0

    def test_earning_is_never_duplicated_for_the_same_sale(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx, points_per_amount=Decimal("1"))
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        first = loyalty_service.earn_for_sale(
            session,
            ctx,
            customer_id=customer.id,
            amount=Decimal("100"),
            reference_type="SALE",
            reference_id=1,
            entry_date=TODAY,
        )
        session.commit()
        second = loyalty_service.earn_for_sale(
            session,
            ctx,
            customer_id=customer.id,
            amount=Decimal("100"),
            reference_type="SALE",
            reference_id=1,
            entry_date=TODAY,
        )
        session.commit()

        assert first is not None
        assert second is None
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 100


class TestRedeemAndAdjust:
    def test_a_customer_can_redeem_points_they_have(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx)
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        loyalty_service.record_adjust(
            session, ctx, customer_id=customer.id, points_delta=100, entry_date=TODAY, note="Starting balance"
        )
        session.commit()

        loyalty_service.record_redeem(session, ctx, customer_id=customer.id, points=40, entry_date=TODAY)
        session.commit()

        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 60

    def test_redeeming_more_than_the_balance_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx)
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(ConflictError):
            loyalty_service.record_redeem(session, ctx, customer_id=customer.id, points=10, entry_date=TODAY)

    def test_redeeming_below_the_minimum_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_program(session, ctx, min_redemption_points=50)
        session.commit()
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        loyalty_service.record_adjust(
            session, ctx, customer_id=customer.id, points_delta=100, entry_date=TODAY, note="Seed"
        )
        session.commit()

        with pytest.raises(InvalidInputError):
            loyalty_service.record_redeem(session, ctx, customer_id=customer.id, points=10, entry_date=TODAY)

    def test_an_adjustment_requires_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(InvalidInputError):
            loyalty_service.record_adjust(
                session, ctx, customer_id=customer.id, points_delta=10, entry_date=TODAY, note="  "
            )

    def test_a_zero_adjustment_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(InvalidInputError):
            loyalty_service.record_adjust(
                session, ctx, customer_id=customer.id, points_delta=0, entry_date=TODAY, note="test"
            )
