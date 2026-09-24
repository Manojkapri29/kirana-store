"""Tenant isolation and RBAC across every Phase 14 surface: shop B can never read or act on shop A's CRM,
loyalty, campaigns, automation rules or referrals, and a role without the permission gets 403."""

import pytest

from app.models.enums import AutomationAction, AutomationTrigger, NotificationChannel, UserRole
from app.services import automation_service, campaign_service, crm_segment_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

API = "/api/v1"
TODAY = today_in_shop_timezone()


@pytest.fixture
def world(session, tenant_a):
    ctx = context_for(tenant_a)
    customer = factories.make_customer(session, tenant_a.shop)
    session.commit()
    group = crm_segment_service.create_manual_group(session, ctx, name="A group", customer_ids=[customer.id])
    campaign = campaign_service.create(
        session, ctx, name="A campaign", description=None, channel=NotificationChannel.IN_APP,
        target_group_id=group.id, target_segment=None, promotion_id=None, message_template="Hi",
    )  # fmt: skip
    rule = automation_service.create_rule(
        session, ctx, name="A rule", trigger_type=AutomationTrigger.NEW_CUSTOMER, conditions={"within_days": 1},
        action_type=AutomationAction.NOTIFY, action_config={}, cooldown_days=30,
    )  # fmt: skip
    session.commit()
    return {"customer": customer.id, "group": group.id, "campaign": campaign.id, "rule": rule.id}


class TestCrossShopReadsAreNotFound:
    @pytest.mark.parametrize(
        "template",
        [
            "/crm/customers/{customer}/profile",
            "/crm/customers/{customer}/timeline",
            "/crm/customers/{customer}/notes",
            "/crm/groups/{group}",
            "/crm/groups/{group}/members",
            "/loyalty/customers/{customer}/ledger",
            "/referrals/customers/{customer}/code",
            "/campaigns/{campaign}",
            "/campaigns/{campaign}/audience",
            "/campaigns/{campaign}/sends",
            "/automation-rules/{rule}",
            "/exports/customer-groups/{group}",
            "/exports/loyalty-ledger/{customer}",
            "/exports/campaigns/{campaign}",
        ],
    )
    def test_another_shops_record_is_not_found(self, client_b, world, template):
        assert client_b.get(API + template.format(**world)).status_code == 404


class TestCrossShopWritesAreNotFound:
    def test_cannot_add_a_note_to_another_shops_customer(self, client_b, world):
        resp = client_b.post(f"{API}/crm/customers/{world['customer']}/notes", json={"body": "x"})
        assert resp.status_code == 404

    def test_cannot_adjust_another_shops_loyalty(self, client_b, world):
        resp = client_b.post(
            f"{API}/loyalty/customers/{world['customer']}/adjust", json={"points_delta": 5, "note": "x"}
        )
        assert resp.status_code == 404

    def test_cannot_launch_another_shops_campaign(self, client_b, world):
        assert client_b.post(f"{API}/campaigns/{world['campaign']}/launch").status_code == 404

    def test_cannot_cancel_another_shops_campaign(self, client_b, world):
        resp = client_b.post(f"{API}/campaigns/{world['campaign']}/cancel", json={"reason": "x"})
        assert resp.status_code == 404

    def test_cannot_run_another_shops_automation_rule(self, client_b, world):
        assert client_b.post(f"{API}/automation-rules/{world['rule']}/run").status_code == 404

    def test_cannot_put_another_shops_customer_in_a_group(self, session, tenant_b, client_b, world):
        group_b = crm_segment_service.create_manual_group(
            session, context_for(tenant_b), name="B group", customer_ids=[]
        )
        session.commit()
        resp = client_b.put(
            f"{API}/crm/groups/{group_b.id}/members", json={"customer_ids": [world["customer"]]}
        )
        assert resp.status_code in (404, 422)

    def test_lists_never_leak_another_shops_rows(self, client_b, world):
        assert client_b.get(f"{API}/campaigns").json()["total"] == 0
        assert client_b.get(f"{API}/crm/groups").json()["items"] == []
        assert client_b.get(f"{API}/automation-rules").json() == {"items": []}


class TestRbac:
    """STAFF (the legacy view-only role) holds no CRM write/launch/automation permission; a missing permission
    is a 403, never a silent success."""

    @pytest.mark.parametrize(
        ("method", "template"),
        [
            ("PATCH", "/crm/customers/{customer}/classification"),
            ("POST", "/crm/customers/{customer}/notes"),
            ("POST", "/loyalty/customers/{customer}/adjust"),
            ("PUT", "/loyalty/program"),
            ("POST", "/campaigns"),
            ("POST", "/campaigns/{campaign}/launch"),
            ("GET", "/automation-rules"),
            ("POST", "/automation-rules/{rule}/run"),
            ("PUT", "/referrals/program"),
            ("PUT", "/crm/approval-settings"),
            ("GET", "/exports/campaigns/{campaign}"),
        ],
    )
    def test_a_role_without_the_permission_is_forbidden(self, make_client, tenant_a, world, method, template):
        staff = make_client(tenant_a, role=UserRole.STAFF)

        resp = staff.request(method, API + template.format(**world), json={})

        assert resp.status_code == 403

    def test_the_owner_can_launch_a_campaign(self, client_a, world):
        assert client_a.post(f"{API}/campaigns/{world['campaign']}/launch").status_code == 200

    def test_a_role_that_can_manage_but_not_launch_cannot_launch(self, make_client, tenant_a, world):
        from app.core.permissions import SYSTEM_ROLES

        launchers = {code for code, perms in SYSTEM_ROLES.items() if "CAMPAIGN_LAUNCH" in perms}
        assert "CASHIER" not in launchers
        assert "SALES_STAFF" not in launchers
