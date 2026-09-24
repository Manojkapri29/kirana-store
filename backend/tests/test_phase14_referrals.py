"""Referral foundation: self-referral prevention, one referred customer per referral, qualifying transaction,
and rewards granted through the existing loyalty ledger (never a separate wallet)."""

from decimal import Decimal

import pytest

from app.models.enums import PaymentMethod, ReferralEventStatus
from app.services import loyalty_service, quick_sale_service, referral_service
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _configure_referral_program(session, ctx, **overrides):
    kwargs = {"is_active": True, "referrer_reward_points": 100, "referred_reward_points": 50}
    kwargs.update(overrides)
    return referral_service.configure_program(session, ctx, **kwargs)


def _configure_loyalty(session, ctx):
    # The smallest positive rate (money has 2 dp) so the qualifying sale itself earns floor(99 * 0.01) = 0 points, isolating the
    # referral rewards in the balance assertions.
    loyalty_service.configure_program(
        session, ctx, is_active=True, points_per_amount=Decimal("0.01"), redemption_value=Decimal("1")
    )


class TestCodes:
    def test_a_customer_gets_a_code_on_request(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        code = referral_service.get_or_create_code(session, ctx, customer.id)
        session.commit()

        assert len(code.code) == 6
        assert code.is_active is True

    def test_requesting_a_code_twice_returns_the_same_code(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        first = referral_service.get_or_create_code(session, ctx, customer.id)
        session.commit()
        second = referral_service.get_or_create_code(session, ctx, customer.id)

        assert first.code == second.code


class TestRegistration:
    def test_self_referral_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, customer.id)
        session.commit()

        with pytest.raises(InvalidInputError):
            referral_service.register_referral(session, ctx, code=code.code, referred_customer_id=customer.id)

    def test_a_customer_can_only_be_referred_once(self, session, tenant_a):
        ctx = context_for(tenant_a)
        referrer = factories.make_customer(session, tenant_a.shop, name="Referrer")
        referred = factories.make_customer(session, tenant_a.shop, name="Referred")
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, referrer.id)
        session.commit()
        referral_service.register_referral(session, ctx, code=code.code, referred_customer_id=referred.id)
        session.commit()

        with pytest.raises(ConflictError):
            referral_service.register_referral(session, ctx, code=code.code, referred_customer_id=referred.id)

    def test_an_unknown_code_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(NotFoundError):
            referral_service.register_referral(session, ctx, code="NOSUCH", referred_customer_id=customer.id)

    def test_a_new_referral_starts_pending(self, session, tenant_a):
        ctx = context_for(tenant_a)
        referrer = factories.make_customer(session, tenant_a.shop, name="Referrer")
        referred = factories.make_customer(session, tenant_a.shop, name="Referred")
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, referrer.id)
        session.commit()

        event = referral_service.register_referral(
            session, ctx, code=code.code, referred_customer_id=referred.id
        )

        assert event.status is ReferralEventStatus.PENDING


class TestQualifyingTransaction:
    def test_a_qualifying_purchase_rewards_both_referrer_and_referred(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_loyalty(session, ctx)
        _configure_referral_program(session, ctx, min_purchase_amount=Decimal("50"))
        session.commit()
        referrer = factories.make_customer(session, tenant_a.shop, name="Referrer")
        referred = factories.make_customer(session, tenant_a.shop, name="Referred")
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, referrer.id)
        session.commit()
        referral_service.register_referral(session, ctx, code=code.code, referred_customer_id=referred.id)
        session.commit()

        entry = quick_sale_service.create_quick_sale(
            session, ctx, {"customer_id": referred.id, "gross_amount": Decimal("99"), "sale_date": TODAY}
        )
        session.commit()
        quick_sale_service.post_quick_sale(session, ctx, entry.sale.id, payment_method=PaymentMethod.CASH)
        session.commit()

        assert loyalty_service.get_balance(session, tenant_a.shop.id, referrer.id) == 100
        assert loyalty_service.get_balance(session, tenant_a.shop.id, referred.id) == 50

    def test_a_purchase_below_the_minimum_does_not_qualify(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_loyalty(session, ctx)
        _configure_referral_program(session, ctx, min_purchase_amount=Decimal("500"))
        session.commit()
        referrer = factories.make_customer(session, tenant_a.shop, name="Referrer")
        referred = factories.make_customer(session, tenant_a.shop, name="Referred")
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, referrer.id)
        session.commit()
        referral_service.register_referral(session, ctx, code=code.code, referred_customer_id=referred.id)
        session.commit()

        entry = quick_sale_service.create_quick_sale(
            session, ctx, {"customer_id": referred.id, "gross_amount": Decimal("10"), "sale_date": TODAY}
        )
        session.commit()
        quick_sale_service.post_quick_sale(session, ctx, entry.sale.id, payment_method=PaymentMethod.CASH)
        session.commit()

        assert loyalty_service.get_balance(session, tenant_a.shop.id, referrer.id) == 0

    def test_max_referrals_per_customer_is_enforced(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _configure_referral_program(session, ctx, max_referrals_per_customer=1)
        session.commit()
        referrer = factories.make_customer(session, tenant_a.shop, name="Referrer")
        first_referred = factories.make_customer(session, tenant_a.shop, name="First")
        second_referred = factories.make_customer(session, tenant_a.shop, name="Second")
        session.commit()
        code = referral_service.get_or_create_code(session, ctx, referrer.id)
        session.commit()
        referral_service.register_referral(
            session, ctx, code=code.code, referred_customer_id=first_referred.id
        )
        session.commit()

        with pytest.raises(ConflictError):
            referral_service.register_referral(
                session, ctx, code=code.code, referred_customer_id=second_referred.id
            )
