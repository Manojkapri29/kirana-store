"""Retention/churn intelligence: repeat purchase rate, at-risk/reactivated purchase patterns (transparent,
explained rules — not unexplained scores), and "not enough historical data" instead of a fabricated
percentage."""

from datetime import timedelta
from decimal import Decimal

from app.models.enums import PaymentMethod
from app.services import quick_sale_service, retention_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _quick_sale(session, ctx, customer_id, sale_date, amount="50"):
    entry = quick_sale_service.create_quick_sale(
        session, ctx, {"customer_id": customer_id, "gross_amount": Decimal(amount), "sale_date": sale_date}
    )
    session.commit()
    quick_sale_service.post_quick_sale(session, ctx, entry.sale.id, payment_method=PaymentMethod.CASH)
    session.commit()
    return entry.sale


class TestPurchasePatterns:
    def test_a_customer_with_one_purchase_has_no_average_interval(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _quick_sale(session, ctx, customer.id, TODAY)

        patterns = retention_service.purchase_patterns(session, tenant_a.shop.id, TODAY)

        row = next(p for p in patterns if p.customer_id == customer.id)
        assert row.average_interval_days is None
        assert row.at_risk is False

    def test_a_regular_customer_who_stops_buying_is_flagged_at_risk_with_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        # Buys every 10 days for a while, then goes silent for 100 days.
        for offset in (100, 90, 80, 70):
            _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=offset))

        patterns = retention_service.purchase_patterns(session, tenant_a.shop.id, TODAY)

        row = next(p for p in patterns if p.customer_id == customer.id)
        assert row.at_risk is True
        assert row.at_risk_reason is not None
        assert "typical interval" in row.at_risk_reason

    def test_a_regular_customer_still_buying_on_schedule_is_not_at_risk(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        for offset in (20, 10, 0):
            _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=offset))

        patterns = retention_service.purchase_patterns(session, tenant_a.shop.id, TODAY)

        row = next(p for p in patterns if p.customer_id == customer.id)
        assert row.at_risk is False


class TestRetentionSummary:
    def test_repeat_purchase_rate_with_no_customers_is_none_not_zero(self, session, tenant_a):
        summary = retention_service.retention_summary(session, tenant_a.shop.id, TODAY)

        assert summary.repeat_purchase_rate is None
        assert summary.customers_with_purchases == 0

    def test_repeat_purchase_rate_counts_customers_with_two_or_more_purchases(self, session, tenant_a):
        ctx = context_for(tenant_a)
        repeat_customer = factories.make_customer(session, tenant_a.shop, name="Repeat")
        one_timer = factories.make_customer(session, tenant_a.shop, name="OneTime")
        session.commit()
        _quick_sale(session, ctx, repeat_customer.id, TODAY - timedelta(days=10))
        _quick_sale(session, ctx, repeat_customer.id, TODAY)
        _quick_sale(session, ctx, one_timer.id, TODAY)

        summary = retention_service.retention_summary(session, tenant_a.shop.id, TODAY)

        assert summary.customers_with_purchases == 2
        assert summary.repeat_customers == 1
        assert summary.repeat_purchase_rate == Decimal("50.0")

    def test_cohort_retention_is_not_enough_data_with_a_small_cohort(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=150))

        summary = retention_service.retention_summary(session, tenant_a.shop.id, TODAY, period_days=90)

        assert summary.cohort_retention_rate == retention_service.NOT_ENOUGH_DATA


class TestReactivationCandidates:
    def test_a_customer_inactive_beyond_the_threshold_is_a_candidate(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=100))

        candidates = retention_service.reactivation_candidates(
            session, tenant_a.shop.id, TODAY, inactive_days=60
        )

        assert customer.id in candidates

    def test_a_recently_active_customer_is_not_a_candidate(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=5))

        candidates = retention_service.reactivation_candidates(
            session, tenant_a.shop.id, TODAY, inactive_days=60
        )

        assert customer.id not in candidates

    def test_a_deactivated_customer_is_excluded(self, session, tenant_a):
        from app.services import customer_service

        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _quick_sale(session, ctx, customer.id, TODAY - timedelta(days=100))
        customer_service.set_customer_active(session, ctx, customer.id, active=False)
        session.commit()

        candidates = retention_service.reactivation_candidates(
            session, tenant_a.shop.id, TODAY, inactive_days=60
        )

        assert customer.id not in candidates
