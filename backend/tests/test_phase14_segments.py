"""Customer segmentation rules and groups: rule evaluation, manual vs. rule-based membership, recalculation,
edge cases (empty data, unknown filters)."""

from decimal import Decimal

import pytest

from app.models.enums import CustomerGroupKind
from app.services import crm_segment_service
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


class TestEvaluateRule:
    def test_an_empty_rule_matches_every_customer(self, session, tenant_a):
        factories.make_customer(session, tenant_a.shop, name="A")
        factories.make_customer(session, tenant_a.shop, name="B")
        session.commit()

        rows = crm_segment_service.evaluate_rule(session, tenant_a.shop.id, TODAY, {})

        assert len(rows) == 2

    def test_an_unknown_filter_key_is_refused(self, session, tenant_a):
        with pytest.raises(InvalidInputError):
            crm_segment_service.evaluate_rule(session, tenant_a.shop.id, TODAY, {"not_a_real_filter": 1})

    def test_min_outstanding_filters_correctly(self, session, tenant_a):
        from app.services import khata_service

        ctx = context_for(tenant_a)
        rich_debtor = factories.make_customer(session, tenant_a.shop, name="Debtor")
        no_debt = factories.make_customer(session, tenant_a.shop, name="Clean")
        session.commit()
        khata_service.create_opening_balance(session, ctx, rich_debtor.id, Decimal("500"))
        session.commit()

        rows = crm_segment_service.evaluate_rule(session, tenant_a.shop.id, TODAY, {"min_outstanding": "100"})

        assert {r.customer_id for r in rows} == {rich_debtor.id}
        assert no_debt.id not in {r.customer_id for r in rows}

    def test_no_customers_at_all_gives_an_empty_result_not_an_error(self, session, tenant_a):
        rows = crm_segment_service.evaluate_rule(
            session, tenant_a.shop.id, TODAY, {"min_total_spend": "1000"}
        )

        assert rows == []


class TestManualGroups:
    def test_a_manual_group_holds_exactly_the_customers_given(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c1 = factories.make_customer(session, tenant_a.shop, name="One")
        c2 = factories.make_customer(session, tenant_a.shop, name="Two")
        session.commit()

        group = crm_segment_service.create_manual_group(
            session, ctx, name="VIPs", customer_ids=[c1.id, c2.id]
        )
        session.commit()

        assert group.kind is CustomerGroupKind.MANUAL
        membership = crm_segment_service.members_of(session, tenant_a.shop.id, group.id)
        assert set(membership.customer_ids) == {c1.id, c2.id}

    def test_setting_manual_members_replaces_the_whole_list(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c1 = factories.make_customer(session, tenant_a.shop, name="One")
        c2 = factories.make_customer(session, tenant_a.shop, name="Two")
        session.commit()
        group = crm_segment_service.create_manual_group(session, ctx, name="Group", customer_ids=[c1.id])
        session.commit()

        crm_segment_service.set_manual_members(session, ctx, group.id, [c2.id])
        session.commit()

        membership = crm_segment_service.members_of(session, tenant_a.shop.id, group.id)
        assert membership.customer_ids == [c2.id]

    def test_a_rule_based_group_cannot_have_its_members_set_directly(self, session, tenant_a):
        ctx = context_for(tenant_a)
        group = crm_segment_service.create_rule_based_group(
            session, ctx, name="Rule Group", rule={}, today=TODAY
        )
        session.commit()

        with pytest.raises(ConflictError):
            crm_segment_service.set_manual_members(session, ctx, group.id, [1])

    def test_group_names_must_be_unique_per_shop(self, session, tenant_a):
        from app.services.errors import ConflictError as ServiceConflictError

        ctx = context_for(tenant_a)
        crm_segment_service.create_manual_group(session, ctx, name="Duplicate", customer_ids=[])
        session.commit()

        with pytest.raises((ServiceConflictError, Exception)):
            crm_segment_service.create_manual_group(session, ctx, name="Duplicate", customer_ids=[])
            session.flush()
        session.rollback()


class TestRuleBasedGroups:
    def test_a_rule_based_group_is_populated_at_creation(self, session, tenant_a):
        ctx = context_for(tenant_a)
        factories.make_customer(session, tenant_a.shop)
        session.commit()

        group = crm_segment_service.create_rule_based_group(
            session, ctx, name="Everyone", rule={}, today=TODAY
        )
        session.commit()

        membership = crm_segment_service.members_of(session, tenant_a.shop.id, group.id)
        assert len(membership.customer_ids) == 1
        assert group.last_recalculated_at is not None

    def test_recalculating_reflects_new_customers(self, session, tenant_a):
        ctx = context_for(tenant_a)
        group = crm_segment_service.create_rule_based_group(session, ctx, name="All", rule={}, today=TODAY)
        session.commit()
        assert crm_segment_service.members_of(session, tenant_a.shop.id, group.id).customer_ids == []

        factories.make_customer(session, tenant_a.shop)
        session.commit()
        crm_segment_service.recalculate(session, ctx, group.id, today=TODAY)
        session.commit()

        assert len(crm_segment_service.members_of(session, tenant_a.shop.id, group.id).customer_ids) == 1

    def test_a_manual_group_cannot_be_recalculated(self, session, tenant_a):
        ctx = context_for(tenant_a)
        group = crm_segment_service.create_manual_group(session, ctx, name="Manual", customer_ids=[])
        session.commit()

        with pytest.raises(ConflictError):
            crm_segment_service.recalculate(session, ctx, group.id, today=TODAY)

    def test_an_invalid_rule_is_refused_at_creation_not_silently_saved(self, session, tenant_a):
        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            crm_segment_service.create_rule_based_group(
                session, ctx, name="Bad", rule={"bogus_key": 1}, today=TODAY
            )
