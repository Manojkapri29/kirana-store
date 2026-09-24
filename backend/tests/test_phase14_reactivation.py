"""Reactivation workflow: preview with explained reasons, consent and cooldown exclusions, and a DRAFT-only
campaign — nothing is ever sent by this workflow."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models.enums import CampaignStatus, NotificationChannel, PaymentMethod
from app.services import campaign_service, crm_service, quick_sale_service, reactivation_service
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()
SMS = NotificationChannel.SMS


def _lapsed_customer(session, tenant, ctx, *, days_ago=100, name="Lapsed", opt_in_sms=True):
    customer = factories.make_customer(session, tenant.shop, name=name)
    session.commit()
    if opt_in_sms:
        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_sms": True})
    entry = quick_sale_service.create_quick_sale(
        session, ctx,
        {"customer_id": customer.id, "gross_amount": Decimal("50"), "sale_date": TODAY - timedelta(days=days_ago)},
    )  # fmt: skip
    session.commit()
    quick_sale_service.post_quick_sale(session, ctx, entry.sale.id, payment_method=PaymentMethod.CASH)
    session.commit()
    return customer


def _preview(session, tenant, **kw):
    kw.setdefault("inactive_days", 60)
    kw.setdefault("cooldown_days", 30)
    kw.setdefault("channel", SMS)
    return reactivation_service.preview(session, tenant.shop.id, TODAY, **kw)


class TestPreview:
    def test_a_lapsed_opted_in_customer_is_eligible_with_a_stated_reason(self, session, tenant_a):
        customer = _lapsed_customer(session, tenant_a, context_for(tenant_a))

        result = _preview(session, tenant_a)

        row = next(r for r in result.rows if r.customer_id == customer.id)
        assert row.verdict == "ELIGIBLE"
        assert "No purchase for 100 days" in row.reason

    def test_a_customer_who_never_opted_in_is_excluded_with_the_reason(self, session, tenant_a):
        customer = _lapsed_customer(session, tenant_a, context_for(tenant_a), opt_in_sms=False)

        result = _preview(session, tenant_a)

        row = next(r for r in result.rows if r.customer_id == customer.id)
        assert row.verdict == "NO_CONSENT"
        assert customer.id not in result.eligible_ids

    def test_opting_out_removes_a_customer_from_the_eligible_list(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = _lapsed_customer(session, tenant_a, ctx)
        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_sms": False})
        session.commit()

        assert customer.id not in _preview(session, tenant_a).eligible_ids

    def test_a_customer_contacted_inside_the_cooldown_is_excluded(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = _lapsed_customer(session, tenant_a, ctx)
        first = reactivation_service.create_draft(
            session, ctx, TODAY, name="Round 1", message_template="Hi", inactive_days=60, cooldown_days=30,
            channel=SMS,
        )  # fmt: skip
        session.commit()
        campaign_service.launch(session, ctx, first.id, TODAY)
        session.commit()

        result = _preview(session, tenant_a, cooldown_days=30)

        row = next(r for r in result.rows if r.customer_id == customer.id)
        assert row.verdict == "IN_COOLDOWN"

    def test_a_zero_day_cooldown_disables_the_frequency_limit(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = _lapsed_customer(session, tenant_a, ctx)
        first = reactivation_service.create_draft(
            session, ctx, TODAY, name="Round 1", message_template="Hi", inactive_days=60, cooldown_days=0,
            channel=SMS,
        )  # fmt: skip
        session.commit()
        campaign_service.launch(session, ctx, first.id, TODAY)
        session.commit()

        assert customer.id in _preview(session, tenant_a, cooldown_days=0).eligible_ids

    def test_a_recently_active_customer_is_not_a_candidate(self, session, tenant_a):
        customer = _lapsed_customer(session, tenant_a, context_for(tenant_a), days_ago=5)

        assert customer.id not in {r.customer_id for r in _preview(session, tenant_a).rows}

    def test_an_invalid_window_is_refused(self, session, tenant_a):
        with pytest.raises(InvalidInputError):
            _preview(session, tenant_a, inactive_days=0)
        with pytest.raises(InvalidInputError):
            _preview(session, tenant_a, cooldown_days=-1)


class TestDraft:
    def test_the_draft_holds_only_eligible_customers_and_sends_nothing(self, session, tenant_a):
        ctx = context_for(tenant_a)
        eligible = _lapsed_customer(session, tenant_a, ctx, name="Eligible")
        _lapsed_customer(session, tenant_a, ctx, name="NoConsent", opt_in_sms=False)

        campaign = reactivation_service.create_draft(
            session, ctx, TODAY, name="Win-back", message_template="We miss you", inactive_days=60,
            cooldown_days=30, channel=SMS,
        )  # fmt: skip
        session.commit()

        assert campaign.status is CampaignStatus.DRAFT
        assert campaign_service.preview_audience(session, tenant_a.shop.id, campaign.id, TODAY) == [
            eligible.id
        ]
        assert campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id) == []

    def test_nothing_eligible_is_a_clear_conflict_not_an_empty_campaign(self, session, tenant_a):
        with pytest.raises(ConflictError):
            reactivation_service.create_draft(
                session, context_for(tenant_a), TODAY, name="x", message_template="x", inactive_days=60,
                cooldown_days=30, channel=SMS,
            )  # fmt: skip


class TestApi:
    def test_preview_endpoint_reports_counts(self, session, tenant_a, client_a):
        _lapsed_customer(session, tenant_a, context_for(tenant_a))

        body = client_a.get("/api/v1/crm/reactivation/preview", params={"channel": "SMS"}).json()

        assert body["eligible_count"] == 1 and body["excluded_count"] == 0

    def test_draft_endpoint_creates_a_draft(self, session, tenant_a, client_a):
        _lapsed_customer(session, tenant_a, context_for(tenant_a))

        resp = client_a.post(
            "/api/v1/crm/reactivation/draft",
            json={"name": "Win-back", "message_template": "Hi", "channel": "SMS"},
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "DRAFT"
