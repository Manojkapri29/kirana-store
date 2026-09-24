"""Marketing automation: trigger conditions, cooldown, idempotency, and the safe actions a rule can take
(a draft campaign, a task, or a staff notification — never a send, a reward or a financial change)."""

from datetime import timedelta

import pytest

from app.models.enums import AutomationAction, AutomationRunStatus, AutomationTrigger, CampaignStatus
from app.services import automation_service
from app.services.errors import InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _create_rule(session, ctx, **overrides):
    kwargs = {
        "name": "Test Rule", "trigger_type": AutomationTrigger.NEW_CUSTOMER, "conditions": {"within_days": 1},
        "action_type": AutomationAction.NOTIFY, "action_config": {}, "cooldown_days": 30,
    }  # fmt: skip
    kwargs.update(overrides)
    return automation_service.create_rule(session, ctx, **kwargs)


class TestNewCustomerTrigger:
    def test_a_new_customer_matches_the_rule(self, session, tenant_a):
        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(session, ctx)
        session.commit()

        result = automation_service.run_rule(session, ctx, rule.id, TODAY)

        assert result.run_status is AutomationRunStatus.SUCCESS
        assert result.customers_actioned == 1

    def test_an_inactive_rule_never_runs(self, session, tenant_a):
        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(session, ctx)
        session.commit()
        automation_service.set_active(session, ctx, rule.id, False)
        session.commit()

        result = automation_service.run_rule(session, ctx, rule.id, TODAY)

        assert result.run_status is AutomationRunStatus.SKIPPED_CONDITION
        assert result.customers_actioned == 0


class TestCooldownAndIdempotency:
    def test_running_the_same_rule_twice_does_not_action_the_same_customer_again(self, session, tenant_a):
        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(session, ctx, cooldown_days=30)
        session.commit()

        first = automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()
        second = automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()

        assert first.customers_actioned == 1
        assert second.customers_actioned == 0
        assert second.run_status is AutomationRunStatus.SKIPPED_CONDITION

    def test_after_the_cooldown_passes_the_rule_can_fire_again(self, session, tenant_a):
        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(session, ctx, cooldown_days=0)
        session.commit()

        first = automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()
        second = automation_service.run_rule(session, ctx, rule.id, TODAY + timedelta(days=1))
        session.commit()

        assert first.customers_actioned == 1
        assert second.customers_actioned == 1

    def test_no_matching_customers_is_a_clean_skip_not_an_error(self, session, tenant_a):
        ctx = context_for(tenant_a)
        rule = _create_rule(session, ctx, conditions={"within_days": 0})
        session.commit()

        result = automation_service.run_rule(session, ctx, rule.id, TODAY - timedelta(days=100))

        assert result.run_status is AutomationRunStatus.SKIPPED_CONDITION
        assert result.customers_matched == 0


class TestInactivityTrigger:
    def test_inactivity_condition_is_required(self, session, tenant_a):
        ctx = context_for(tenant_a)
        rule = _create_rule(session, ctx, trigger_type=AutomationTrigger.INACTIVITY, conditions={})
        session.commit()

        with pytest.raises(InvalidInputError):
            automation_service.run_rule(session, ctx, rule.id, TODAY)

    def test_inactivity_respects_marketing_opt_in_by_default(self, session, tenant_a):
        from decimal import Decimal

        from app.models.enums import PaymentMethod
        from app.services import crm_service, quick_sale_service

        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        entry = quick_sale_service.create_quick_sale(
            session, ctx,
            {"customer_id": customer.id, "gross_amount": Decimal("50"), "sale_date": TODAY - timedelta(days=200)},
        )  # fmt: skip
        session.commit()
        quick_sale_service.post_quick_sale(session, ctx, entry.sale.id, payment_method=PaymentMethod.CASH)
        session.commit()
        rule = _create_rule(
            session, ctx, trigger_type=AutomationTrigger.INACTIVITY,
            conditions={"inactive_days": 60, "requires_marketing_opt_in": True},
        )  # fmt: skip
        session.commit()

        result = automation_service.run_rule(session, ctx, rule.id, TODAY)
        assert result.customers_matched == 0  # never opted in

        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_email": True})
        session.commit()
        result = automation_service.run_rule(session, ctx, rule.id, TODAY)
        assert result.customers_matched == 1


class TestActions:
    def test_create_campaign_draft_creates_a_draft_never_a_launched_campaign(self, session, tenant_a):
        from app.services import campaign_service

        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(
            session, ctx, action_type=AutomationAction.CREATE_CAMPAIGN_DRAFT,
            action_config={"channel": "IN_APP", "message_template": "Welcome!"},
        )  # fmt: skip
        session.commit()

        automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()

        campaigns, total = campaign_service.list_campaigns(session, tenant_a.shop.id)
        assert total == 1
        assert campaigns[0].status is CampaignStatus.DRAFT

    def test_create_task_creates_one_task_per_matched_customer(self, session, tenant_a):
        from app.services import task_service

        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(
            session, ctx, action_type=AutomationAction.CREATE_TASK, action_config={"title": "Follow up"}
        )
        session.commit()

        automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()

        tasks, total = task_service.list_tasks(session, tenant_a.shop.id)
        assert total == 1
        assert tasks[0].title == "Follow up"


class TestFailureHandling:
    def test_a_failing_action_is_recorded_as_failed_and_can_be_retried(self, session, tenant_a, monkeypatch):
        from app.services import task_service

        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()
        rule = _create_rule(
            session, ctx, action_type=AutomationAction.CREATE_TASK, action_config={"title": "x"}
        )
        session.commit()

        def boom(*args, **kwargs):
            raise RuntimeError("task store unavailable")

        monkeypatch.setattr(task_service, "create", boom)
        failed = automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()

        assert failed.run_status is AutomationRunStatus.FAILED
        assert "task store unavailable" in failed.detail
        assert failed.customers_actioned == 0

        monkeypatch.undo()
        retried = automation_service.run_rule(session, ctx, rule.id, TODAY)
        session.commit()

        assert retried.run_status is AutomationRunStatus.SUCCESS  # a failure does not start the cooldown
        assert retried.customers_actioned == 1
