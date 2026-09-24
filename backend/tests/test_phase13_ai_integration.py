"""AI integration for Phase 13: the TASK_DRAFT action (propose -> confirm -> executed, never auto-created), and
the two new read-only tools (dead stock, supplier analytics) enforcing their declared permission and never
inventing a number that is not in the underlying service's output."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import BusinessTask
from app.services import ai_tools, inventory_intelligence_service, supplier_intelligence_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

API = "/api/v1/ai"
TODAY = today_in_shop_timezone()


def propose(client, kind, payload, feature="assistant", expect=201):
    response = client.post(f"{API}/actions", json={"kind": kind, "feature": feature, "payload": payload})
    assert response.status_code == expect, response.text
    return response.json()


def confirm(client, action, expect=200):
    response = client.post(f"{API}/actions/{action['id']}/confirm")
    assert response.status_code == expect, response.text
    return response.json()


class TestTaskDraftAction:
    def test_proposing_a_task_creates_nothing_until_confirmed(self, session_factory, client_a):
        before = session_factory().execute(select(func.count()).select_from(BusinessTask)).scalar()

        action = propose(client_a, "TASK_DRAFT", {"title": "Recount the rice shelf"})

        assert action["status"] == "PROPOSED"
        assert action["preview"]["title"] == "Create Task"
        after = session_factory().execute(select(func.count()).select_from(BusinessTask)).scalar()
        assert after == before

    def test_confirming_creates_exactly_one_task_through_task_service(self, session_factory, client_a):
        action = propose(client_a, "TASK_DRAFT", {"title": "Call the supplier"})

        done = confirm(client_a, action)

        assert done["status"] == "EXECUTED" and done["result_type"] == "task"
        with session_factory() as s:
            task = s.get(BusinessTask, done["result_ids"][0])
            assert task.title == "Call the supplier"

    def test_confirming_twice_does_not_create_a_second_task(self, session_factory, client_a):
        action = propose(client_a, "TASK_DRAFT", {"title": "One-off job"})
        confirm(client_a, action)
        before = session_factory().execute(select(func.count()).select_from(BusinessTask)).scalar()

        confirm(client_a, action, expect=409)

        after = session_factory().execute(select(func.count()).select_from(BusinessTask)).scalar()
        assert after == before

    def test_a_blank_title_is_refused_at_proposal_not_silently_accepted(self, client_a):
        propose(client_a, "TASK_DRAFT", {"title": ""}, expect=422)


class TestNewIntelligenceTools:
    def test_dead_stock_tool_is_gated_by_inventory_view_permission(self):
        assert ai_tools.TOOL_PERMISSION["get_dead_stock"] == "INVENTORY_VIEW"
        assert "get_dead_stock" in ai_tools.TOOLS

    def test_supplier_analytics_tool_is_gated_by_the_analytics_and_supplier_permissions(self):
        # Phase 16 made this tool period-aware and analytics-grade: it now needs ANALYTICS_ADVANCED plus the supplier data.
        assert ai_tools.TOOL_PERMISSION["get_supplier_analytics"] == "ANALYTICS_ADVANCED"
        assert set(ai_tools.TOOL_EXTRA_PERMISSIONS["get_supplier_analytics"]) == {"SUPPLIER_VIEW", "PURCHASE_VIEW"}
        assert "get_supplier_analytics" in ai_tools.TOOLS

    def test_every_tool_has_exactly_one_permission_entry(self):
        assert set(ai_tools.TOOLS) == set(ai_tools.TOOL_PERMISSION)

    def test_the_dead_stock_tool_reports_the_same_figures_as_the_service_it_wraps(self, session, tenant_a):
        from app.services import inventory_service

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, name="Unsold Widget")
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(10), unit_cost=Decimal("7")
        )
        session.commit()

        direct = inventory_intelligence_service.dead_stock(session, tenant_a.shop.id, TODAY, days=90)
        tc = ai_tools.ToolContext(session=session, ctx=ctx, today=TODAY)
        answer = ai_tools.TOOLS["get_dead_stock"].run(tc, ai_tools.NoArgs())

        assert any(row.product_id == p.id for row in direct)
        assert any(
            r[0] == "Unsold Widget" for r in answer.table.rows
        )  # the tool's own table, not a paraphrase

    def test_the_supplier_analytics_tool_never_invents_a_value_the_service_did_not_return(
        self, session, tenant_a
    ):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()

        row = supplier_intelligence_service.analytics_for(session, tenant_a.shop.id, supplier.id)

        assert row.purchase_count == 0
        assert row.total_value == Decimal("0")
        assert row.average_purchase_value is None  # never fabricated as 0
