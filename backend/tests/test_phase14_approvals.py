"""CRM safety controls: a campaign launch to an audience over the shop's configured threshold, and a manual
loyalty adjustment over the configured size, each need a SEPARATE person's approval. No threshold configured
means no extra approval — there is no hardcoded number."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.context import RequestContext
from app.models import AuditLog
from app.models.enums import CampaignStatus, NotificationChannel
from app.services import approval_service, campaign_service, crm_segment_service, crm_service, loyalty_service
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _campaign_over(session, tenant, ctx, size):
    customers = [factories.make_customer(session, tenant.shop, name=f"C{i}") for i in range(size)]
    session.commit()
    group = crm_segment_service.create_manual_group(
        session, ctx, name="G", customer_ids=[c.id for c in customers]
    )
    session.commit()
    campaign = campaign_service.create(
        session, ctx, name="Big", description=None, channel=NotificationChannel.IN_APP,
        target_group_id=group.id, target_segment=None, promotion_id=None, message_template="Hi",
    )  # fmt: skip
    session.commit()
    return campaign


def _second_ctx(session, tenant, ctx):
    other = factories.make_user(session, tenant.shop, email="approver@test.local")
    session.commit()
    return RequestContext(shop_id=tenant.shop.id, user_id=other.id, role=ctx.role)


class TestCampaignLaunchApproval:
    def test_no_threshold_configured_means_no_extra_approval(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _campaign_over(session, tenant_a, ctx, 3)

        launched = campaign_service.launch(session, ctx, campaign.id, TODAY)

        assert launched.status is CampaignStatus.COMPLETED
        assert launched.requires_approval is False

    def test_an_audience_at_the_threshold_launches_normally(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=3)
        campaign = _campaign_over(session, tenant_a, ctx, 3)

        assert campaign_service.launch(session, ctx, campaign.id, TODAY).status is CampaignStatus.COMPLETED

    def test_an_audience_over_the_threshold_waits_for_approval_and_sends_nothing(
        self, session, tenant_a, set_shop
    ):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)

        held = campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        assert held.status is CampaignStatus.DRAFT
        assert held.requires_approval is True
        assert campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id) == []
        pending = approval_service.list_pending(session, tenant_a.shop.id)
        assert [p.kind for p in pending] == [campaign_service.APPROVAL_KIND]
        assert pending[0].observed_value == Decimal(3)
        assert pending[0].threshold_value == Decimal(2)

    def test_asking_again_while_pending_does_not_open_a_second_request(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        assert len(approval_service.list_pending(session, tenant_a.shop.id)) == 1

    def test_the_requester_cannot_approve_their_own_launch(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        request = approval_service.list_pending(session, tenant_a.shop.id)[0]

        with pytest.raises(ForbiddenError):
            approval_service.decide(session, ctx, request.id, approve=True)

    def test_after_a_second_person_approves_the_launch_goes_through_and_is_audited(
        self, session, tenant_a, set_shop
    ):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        request = approval_service.list_pending(session, tenant_a.shop.id)[0]
        approver = _second_ctx(session, tenant_a, ctx)
        approval_service.decide(session, approver, request.id, approve=True)
        campaign_service.apply_approval_decision(session, approver, campaign.id, approved=True)
        session.commit()

        launched = campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        assert launched.status is CampaignStatus.COMPLETED
        assert launched.requires_approval is False
        assert len(campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)) == 3
        actions = set(session.scalars(select(AuditLog.action)))
        assert {"approval_requested", "approval_decided", "campaign_launched"} <= actions

    def test_a_rejected_launch_is_not_sent_and_a_new_request_can_be_opened(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        request = approval_service.list_pending(session, tenant_a.shop.id)[0]
        approver = _second_ctx(session, tenant_a, ctx)
        approval_service.decide(session, approver, request.id, approve=False, note="Too broad")
        campaign_service.apply_approval_decision(session, approver, campaign.id, approved=False)
        session.commit()

        held = campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        assert held.status is CampaignStatus.DRAFT
        assert campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id) == []
        assert len(approval_service.list_pending(session, tenant_a.shop.id)) == 1


class TestLoyaltyAdjustmentApproval:
    def _adjust(self, session, ctx, customer_id, delta, **kwargs):
        return loyalty_service.adjust_with_controls(
            session,
            ctx,
            customer_id=customer_id,
            points_delta=delta,
            entry_date=TODAY,
            note="Goodwill",
            **kwargs,
        )

    def test_no_threshold_configured_records_immediately(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        outcome = self._adjust(session, ctx, customer.id, 10_000)
        session.commit()

        assert outcome.entry is not None and outcome.approval_request_id is None
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 10_000

    def test_a_small_adjustment_is_recorded_immediately(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        outcome = self._adjust(session, ctx, customer.id, 100)

        assert outcome.entry is not None

    def test_a_large_adjustment_changes_no_balance_until_approved(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        outcome = self._adjust(session, ctx, customer.id, 500)
        session.commit()

        assert outcome.entry is None and outcome.approval_request_id is not None
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 0

    def test_an_unapproved_request_cannot_be_redeemed(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        pending = self._adjust(session, ctx, customer.id, 500)
        session.commit()

        with pytest.raises(ConflictError):
            self._adjust(session, ctx, customer.id, 500, approval_request_id=pending.approval_request_id)

    def test_an_approved_request_records_the_adjustment_exactly_once(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        pending = self._adjust(session, ctx, customer.id, 500)
        session.commit()
        approval_service.decide(
            session, _second_ctx(session, tenant_a, ctx), pending.approval_request_id, approve=True
        )
        session.commit()

        done = self._adjust(session, ctx, customer.id, 500, approval_request_id=pending.approval_request_id)
        session.commit()
        assert done.entry is not None
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 500

        with pytest.raises(ConflictError):
            self._adjust(session, ctx, customer.id, 500, approval_request_id=pending.approval_request_id)
        session.rollback()
        assert loyalty_service.get_balance(session, tenant_a.shop.id, customer.id) == 500

    def test_an_approval_cannot_be_reused_for_a_bigger_adjustment(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        pending = self._adjust(session, ctx, customer.id, 500)
        session.commit()
        approval_service.decide(
            session, _second_ctx(session, tenant_a, ctx), pending.approval_request_id, approve=True
        )
        session.commit()

        with pytest.raises(InvalidInputError):
            self._adjust(session, ctx, customer.id, 5000, approval_request_id=pending.approval_request_id)

    def test_an_approval_for_one_customer_cannot_be_used_for_another(self, session, tenant_a, set_shop):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_loyalty_adjustment_threshold=100)
        first = factories.make_customer(session, tenant_a.shop, name="First")
        second = factories.make_customer(session, tenant_a.shop, name="Second")
        session.commit()
        pending = self._adjust(session, ctx, first.id, 500)
        session.commit()
        approval_service.decide(
            session, _second_ctx(session, tenant_a, ctx), pending.approval_request_id, approve=True
        )
        session.commit()

        with pytest.raises(InvalidInputError):
            self._adjust(session, ctx, second.id, 500, approval_request_id=pending.approval_request_id)


class TestApprovalSettings:
    def test_settings_are_off_by_default(self, session, tenant_a):
        assert crm_service.get_approval_settings(session, tenant_a.shop.id) == {
            "campaign_audience_threshold": None,
            "loyalty_adjustment_threshold": None,
        }

    def test_changing_settings_is_audited(self, session, tenant_a):
        ctx = context_for(tenant_a)

        crm_service.set_approval_settings(
            session, ctx, campaign_audience_threshold=50, loyalty_adjustment_threshold=1000
        )
        session.commit()

        assert "crm_approval_settings_changed" in set(session.scalars(select(AuditLog.action)))

    def test_a_negative_threshold_is_refused(self, session, tenant_a):
        with pytest.raises(InvalidInputError):
            crm_service.set_approval_settings(
                session,
                context_for(tenant_a),
                campaign_audience_threshold=-1,
                loyalty_adjustment_threshold=None,
            )


class TestApprovalsApi:
    def test_the_decide_endpoint_clears_a_rejected_campaigns_waiting_flag(
        self, session, tenant_a, set_shop, make_client, fresh
    ):
        ctx = context_for(tenant_a)
        set_shop(tenant_a, crm_campaign_audience_threshold=2)
        campaign = _campaign_over(session, tenant_a, ctx, 3)
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        request = approval_service.list_pending(session, tenant_a.shop.id)[0]
        other = factories.make_user(session, tenant_a.shop, email="api-approver@test.local")
        session.commit()
        from app.models.enums import UserRole

        approver = RequestContext(shop_id=tenant_a.shop.id, user_id=other.id, role=UserRole.OWNER)
        from fastapi.testclient import TestClient

        from app.api.deps import get_request_context
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_request_context] = lambda: approver
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/approvals/{request.id}/decide", json={"approve": False, "note": "No"}
            )
        assert resp.status_code == 200, resp.text
        assert fresh(lambda s: s.get(type(campaign), campaign.id).requires_approval) is False
