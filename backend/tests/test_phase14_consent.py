"""Communication preferences: marketing consent is per channel, defaults to OFF, is changed only by an
audited action, gates campaigns, and is separate from transactional messages (which never look at it)."""

import inspect

from sqlalchemy import select

from app.models import AuditLog
from app.services import crm_service, notification_service
from tests import factories
from tests.conftest import context_for


class TestConsent:
    def test_every_channel_defaults_to_no_marketing_consent(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        assert not any(
            (
                customer.marketing_opt_in_email, customer.marketing_opt_in_sms,
                customer.marketing_opt_in_whatsapp, customer.marketing_opt_in_push,
            )
        )  # fmt: skip

    def test_a_consent_change_is_audited_with_before_and_after(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_sms": True})
        session.commit()

        rows = list(
            session.scalars(
                select(AuditLog).where(AuditLog.entity_type == "customer", AuditLog.entity_id == customer.id)
            )
        )
        changed = [r for r in rows if r.after_json and "marketing_opt_in_sms" in r.after_json]
        assert changed, "the consent change must leave an audit row"
        assert changed[-1].after_json["marketing_opt_in_sms"] is True
        assert changed[-1].user_id == ctx.user_id

    def test_consent_is_per_channel(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        crm_service.update_classification(session, ctx, customer.id, {"marketing_opt_in_email": True})
        session.commit()

        assert customer.marketing_opt_in_email is True
        assert customer.marketing_opt_in_sms is False

    def test_transactional_notifications_never_consult_marketing_consent(self):
        source = inspect.getsource(notification_service)
        assert "marketing_opt_in" not in source

    def test_the_api_changes_consent_only_through_the_crm_route(self, session, tenant_a, client_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        resp = client_a.patch(
            f"/api/v1/crm/customers/{customer.id}/classification", json={"marketing_opt_in_whatsapp": True}
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["marketing_opt_in_whatsapp"] is True
