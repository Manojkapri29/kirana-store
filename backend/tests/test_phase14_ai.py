"""AI integration for Phase 14: CAMPAIGN_DRAFT is propose -> confirm -> a DRAFT campaign only (never launched,
never sent), and the read-only CRM tools enforce their declared permission and only echo what the underlying
services return."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import Campaign, CampaignSend, LoyaltyLedger
from app.models.enums import CampaignStatus
from app.services import ai_tools, loyalty_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

API = "/api/v1/ai"
TODAY = today_in_shop_timezone()

NEW_TOOLS = {
    "get_customer_profile": "CRM_VIEW",
    "get_customer_segments": "CRM_VIEW",
    "get_customer_retention_summary": "CRM_ANALYTICS_VIEW",
    "get_inactive_customers": "CRM_VIEW",
    "get_reactivation_candidates": "CRM_ANALYTICS_VIEW",
    "get_loyalty_summary": "LOYALTY_VIEW",
    "get_campaign_summary": "CAMPAIGN_VIEW",
    "get_referral_summary": "REFERRAL_VIEW",
    "get_customer_growth_dashboard": "CRM_ANALYTICS_VIEW",
}


def _propose(client, payload, expect=201):
    response = client.post(
        f"{API}/actions", json={"kind": "CAMPAIGN_DRAFT", "feature": "assistant", "payload": payload}
    )
    assert response.status_code == expect, response.text
    return response.json()


def _count(session_factory, model):
    with session_factory() as s:
        return s.execute(select(func.count()).select_from(model)).scalar()


class TestCampaignDraftAction:
    PAYLOAD = {"name": "Win-back", "target_segment": "INACTIVE", "message_template": "We miss you!"}

    def test_proposing_creates_no_campaign(self, session_factory, client_a):
        action = _propose(client_a, self.PAYLOAD)

        assert action["status"] == "PROPOSED"
        assert _count(session_factory, Campaign) == 0

    def test_confirming_creates_a_draft_and_never_launches_or_sends(self, session_factory, client_a):
        action = _propose(client_a, self.PAYLOAD)

        done = client_a.post(f"{API}/actions/{action['id']}/confirm")

        assert done.status_code == 200, done.text
        body = done.json()
        assert body["status"] == "EXECUTED"
        with session_factory() as s:
            campaign = s.get(Campaign, body["result_ids"][0])
            assert campaign.status is CampaignStatus.DRAFT
            assert campaign.launched_at is None
        assert _count(session_factory, CampaignSend) == 0

    def test_confirming_twice_does_not_create_a_second_campaign(self, session_factory, client_a):
        action = _propose(client_a, self.PAYLOAD)
        client_a.post(f"{API}/actions/{action['id']}/confirm")

        again = client_a.post(f"{API}/actions/{action['id']}/confirm")

        assert again.status_code == 409
        assert _count(session_factory, Campaign) == 1

    def test_a_blank_message_is_refused_at_proposal(self, client_a):
        _propose(client_a, {**self.PAYLOAD, "message_template": ""}, expect=422)

    def test_a_payload_cannot_smuggle_in_a_launch_or_status(self, client_a):
        _propose(client_a, {**self.PAYLOAD, "status": "RUNNING"}, expect=422)

    def test_the_action_is_gated_by_the_campaign_manage_permission(self):
        from app.models.enums import AiActionKind
        from app.services.ai_action_service import ACTION_PERMISSION

        assert ACTION_PERMISSION[AiActionKind.CAMPAIGN_DRAFT] == "CAMPAIGN_MANAGE"


class TestCrmTools:
    def test_each_new_tool_is_registered_with_its_declared_permission(self):
        for name, permission in NEW_TOOLS.items():
            assert name in ai_tools.TOOLS
            assert ai_tools.TOOL_PERMISSION[name] == permission

    def test_tools_and_permissions_stay_in_key_sync(self):
        assert set(ai_tools.TOOLS) == set(ai_tools.TOOL_PERMISSION)

    def test_no_crm_tool_can_write(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        ctx = context_for(tenant_a)
        tc = ai_tools.ToolContext(session=session, ctx=ctx, today=TODAY)
        before_ledger = session.execute(select(func.count()).select_from(LoyaltyLedger)).scalar()

        for name in ("get_loyalty_summary", "get_campaign_summary", "get_referral_summary"):
            ai_tools.TOOLS[name].run(tc, ai_tools.NoArgs())
        ai_tools.TOOLS["get_customer_profile"].run(tc, ai_tools.CustomerArgs(customer_id=customer.id))

        assert session.execute(select(func.count()).select_from(LoyaltyLedger)).scalar() == before_ledger

    def test_the_loyalty_tool_says_not_configured_rather_than_inventing_figures(self, session, tenant_a):
        tc = ai_tools.ToolContext(session=session, ctx=context_for(tenant_a), today=TODAY)

        answer = ai_tools.TOOLS["get_loyalty_summary"].run(tc, ai_tools.NoArgs())

        assert answer.status == "NOT_CONFIGURED"
        assert answer.figures == []

    def test_the_loyalty_tool_reports_the_ledger_derived_figures(self, session, tenant_a):
        ctx = context_for(tenant_a)
        loyalty_service.configure_program(
            session, ctx, is_active=True, points_per_amount=Decimal("1"), redemption_value=Decimal("1")
        )
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        loyalty_service.record_adjust(
            session, ctx, customer_id=customer.id, points_delta=40, entry_date=TODAY, note="Welcome"
        )
        session.commit()
        tc = ai_tools.ToolContext(session=session, ctx=ctx, today=TODAY)

        answer = ai_tools.TOOLS["get_loyalty_summary"].run(tc, ai_tools.NoArgs())

        assert answer.status == "ANSWERED"
        assert any("40" in f.value for f in answer.figures)

    def test_an_unknown_customer_is_reported_as_no_data_not_invented(self, session, tenant_a):
        tc = ai_tools.ToolContext(session=session, ctx=context_for(tenant_a), today=TODAY)

        answer = ai_tools.TOOLS["get_customer_profile"].run(tc, ai_tools.CustomerArgs(customer_id=999_999))

        assert answer.status == "NO_DATA"
        assert answer.figures == []
