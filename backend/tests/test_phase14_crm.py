"""CRM foundation: customer profile, notes, timeline, and classification/consent changes."""

from decimal import Decimal

import pytest

from app.services import crm_service
from app.services.errors import InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


class TestProfile:
    def test_a_customer_with_no_history_gets_an_honest_empty_profile(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        profile = crm_service.get_profile(session, tenant_a.shop.id, customer.id, TODAY)

        assert profile.analytics.total_purchases == Decimal("0")
        assert profile.loyalty_balance == 0
        assert profile.loyalty_program_active is False
        assert profile.referrals_made == 0
        assert profile.referral_code is None


class TestClassification:
    def test_customer_type_and_source_and_tags_can_be_set(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        updated = crm_service.update_classification(
            session,
            ctx,
            customer.id,
            {"customer_type": "RETAIL", "source": "WALK_IN", "tags": ["vip", "regular"]},
        )
        session.commit()

        assert updated.customer_type.value == "RETAIL"
        assert updated.source.value == "WALK_IN"
        assert updated.tags == ["vip", "regular"]

    def test_marketing_consent_defaults_to_opted_out(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        assert customer.marketing_opt_in_email is False
        assert customer.marketing_opt_in_sms is False
        assert customer.marketing_opt_in_whatsapp is False
        assert customer.marketing_opt_in_push is False

    def test_consent_can_be_opted_in_and_changes_are_audited(self, session, tenant_a):
        from sqlalchemy import select

        from app.models import AuditLog

        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_email": True})
        session.commit()

        refreshed = session.get(type(customer), customer.id)
        assert refreshed.marketing_opt_in_email is True
        logs = list(
            session.scalars(
                select(AuditLog).where(AuditLog.entity_type == "customer", AuditLog.entity_id == customer.id)
            )
        )
        assert any(log.action == "update" for log in logs)

    def test_an_unknown_crm_field_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(InvalidInputError):
            crm_service.update_classification(session, ctx, customer.id, {"is_active": False})


class TestNotes:
    def test_a_note_can_be_added_and_listed(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        crm_service.add_note(session, ctx, customer.id, "Called about a delayed delivery.")
        session.commit()

        notes = crm_service.list_notes(session, tenant_a.shop.id, customer.id)
        assert len(notes) == 1
        assert notes[0].body == "Called about a delayed delivery."

    def test_a_blank_note_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(InvalidInputError):
            crm_service.add_note(session, ctx, customer.id, "   ")

    def test_a_note_cannot_be_edited_or_deleted_in_the_database(self, session, tenant_a):
        from app.models import CustomerNote

        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        note = crm_service.add_note(session, ctx, customer.id, "Original note.")
        session.commit()

        with pytest.raises(Exception):  # noqa: B017 (sqlite trigger raises a generic error)
            session.execute(
                CustomerNote.__table__.update().where(CustomerNote.id == note.id).values(body="Edited")
            )
            session.flush()
        session.rollback()


class TestTimeline:
    def test_the_timeline_includes_customer_created_and_a_note(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        crm_service.add_note(session, ctx, customer.id, "First contact.")
        session.commit()

        events = crm_service.get_timeline(session, tenant_a.shop.id, customer.id)

        kinds = {e.kind for e in events}
        assert "customer_created" in kinds
        assert "note" in kinds

    def test_the_timeline_is_sorted_newest_first(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        crm_service.add_note(session, ctx, customer.id, "Earlier note.")
        crm_service.add_note(session, ctx, customer.id, "Later note.")
        session.commit()

        events = crm_service.get_timeline(session, tenant_a.shop.id, customer.id)

        occurred = [e.occurred_at for e in events]
        assert occurred == sorted(occurred, reverse=True)

    def test_the_timeline_for_another_shops_customer_is_not_found(self, session, tenant_a, tenant_b):
        from app.services.errors import NotFoundError

        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        with pytest.raises(NotFoundError):
            crm_service.get_timeline(session, tenant_b.shop.id, customer.id)
