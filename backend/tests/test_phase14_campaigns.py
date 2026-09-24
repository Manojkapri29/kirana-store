"""Campaign lifecycle: audience calculation, launch (honest NOT_CONFIGURED sends, since no real provider
exists), pause/resume/cancel, scheduling, and consent enforcement."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import CampaignSendStatus, CampaignStatus, NotificationChannel
from app.services import campaign_service, crm_segment_service, crm_service
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _create(session, ctx, **overrides):
    kwargs = {
        "name": "Test Campaign", "description": None, "channel": NotificationChannel.IN_APP,
        "target_group_id": None, "target_segment": "ACTIVE", "promotion_id": None,
        "message_template": "Hello!",
    }  # fmt: skip
    kwargs.update(overrides)
    return campaign_service.create(session, ctx, **kwargs)


class TestCreate:
    def test_a_campaign_starts_as_draft(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()

        assert campaign.status is CampaignStatus.DRAFT

    def test_a_campaign_must_target_a_group_or_a_segment_not_both(self, session, tenant_a):
        ctx = context_for(tenant_a)
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[])
        session.commit()

        with pytest.raises(InvalidInputError):
            _create(session, ctx, target_group_id=group.id, target_segment="ACTIVE")

    def test_a_campaign_must_target_something(self, session, tenant_a):
        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            _create(session, ctx, target_segment=None)

    def test_a_blank_message_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            _create(session, ctx, message_template="   ")


class TestAudience:
    def test_the_audience_is_computed_from_the_target_group(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[customer.id])
        session.commit()
        campaign = _create(session, ctx, target_group_id=group.id, target_segment=None)
        session.commit()

        ids = campaign_service.preview_audience(session, tenant_a.shop.id, campaign.id, TODAY)

        assert ids == [customer.id]

    def test_a_deactivated_customer_is_never_in_the_audience(self, session, tenant_a):
        from app.services import customer_service

        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[customer.id])
        session.commit()
        customer_service.set_customer_active(session, ctx, customer.id, active=False)
        session.commit()
        campaign = _create(session, ctx, target_group_id=group.id, target_segment=None)
        session.commit()

        ids = campaign_service.preview_audience(session, tenant_a.shop.id, campaign.id, TODAY)

        assert ids == []


class TestLaunch:
    def test_launching_snapshots_the_audience_and_records_an_honest_outcome_per_customer(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[customer.id])
        session.commit()
        campaign = _create(
            session, ctx, channel=NotificationChannel.IN_APP, target_group_id=group.id, target_segment=None
        )
        session.commit()

        launched = campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        assert launched.status is CampaignStatus.COMPLETED
        outcomes = campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)
        assert len(outcomes) == 1
        # No real provider exists anywhere in this codebase (and no consent field applies to IN_APP), so the
        # honest outcome is NOT_CONFIGURED, never a fabricated SENT.
        assert outcomes[0].status is CampaignSendStatus.NOT_CONFIGURED

    def test_a_customer_who_never_opted_in_to_the_channel_is_skipped_not_sent_to(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[customer.id])
        session.commit()
        campaign = _create(
            session, ctx, channel=NotificationChannel.SMS, target_group_id=group.id, target_segment=None
        )
        session.commit()

        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        outcomes = campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)
        assert outcomes[0].status is CampaignSendStatus.SKIPPED_NO_CONSENT

    def test_a_customer_who_opted_in_still_gets_not_configured_since_no_provider_exists(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_sms": True})
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=[customer.id])
        session.commit()
        campaign = _create(
            session, ctx, channel=NotificationChannel.SMS, target_group_id=group.id, target_segment=None
        )
        session.commit()

        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        outcomes = campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)
        assert outcomes[0].status is CampaignSendStatus.NOT_CONFIGURED

    def test_a_completed_campaign_cannot_be_launched_again(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        with pytest.raises(ConflictError):
            campaign_service.launch(session, ctx, campaign.id, TODAY)


class TestLifecycle:
    def test_pause_and_resume(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()
        campaign.status = CampaignStatus.RUNNING  # simulate an in-flight campaign
        session.commit()

        paused = campaign_service.pause(session, ctx, campaign.id)
        session.commit()
        assert paused.status is CampaignStatus.PAUSED

        resumed = campaign_service.resume(session, ctx, campaign.id)
        session.commit()
        assert resumed.status is CampaignStatus.RUNNING

    def test_cancel_requires_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()

        with pytest.raises(InvalidInputError):
            campaign_service.cancel(session, ctx, campaign.id, "  ")

    def test_a_cancelled_campaign_cannot_be_cancelled_again(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()
        campaign_service.cancel(session, ctx, campaign.id, "No longer relevant")
        session.commit()

        with pytest.raises(ConflictError):
            campaign_service.cancel(session, ctx, campaign.id, "Again")

    def test_schedule_moves_a_draft_to_scheduled(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()

        scheduled = campaign_service.schedule(
            session, ctx, campaign.id, datetime.now(UTC) + timedelta(days=1)
        )
        session.commit()

        assert scheduled.status is CampaignStatus.SCHEDULED

    def test_only_a_draft_or_scheduled_campaign_can_be_edited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        campaign = _create(session, ctx)
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()

        with pytest.raises(ConflictError):
            campaign_service.update(session, ctx, campaign.id, {"name": "New Name"})
