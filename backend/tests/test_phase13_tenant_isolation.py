"""Tenant isolation across every new Phase 13 surface: a user in shop B must never read or act on shop A's
stock counts, tasks, approvals, scheduled reports, or intelligence data."""

from decimal import Decimal

from app.models.enums import StockCountScope
from tests import factories
from tests.conftest import context_for


class TestCrossShopAccessIsRefused:
    def test_a_stock_count_from_another_shop_is_not_found(self, session, tenant_a, tenant_b, client_b):
        from app.services import stock_count_service

        ctx_a = context_for(tenant_a)
        factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        count = stock_count_service.create(session, ctx_a, title="A's count", scope=StockCountScope.FULL)
        session.commit()

        resp = client_b.get(f"/api/v1/stock-counts/{count.id}")
        assert resp.status_code == 404

    def test_a_task_from_another_shop_is_not_found(self, session, tenant_a, tenant_b, client_b):
        from app.services import task_service

        ctx_a = context_for(tenant_a)
        task = task_service.create(session, ctx_a, title="A's task")
        session.commit()

        resp = client_b.get(f"/api/v1/tasks/{task.id}")
        assert resp.status_code == 404

    def test_an_approval_request_from_another_shop_is_not_found(self, session, tenant_a, tenant_b, client_b):
        from app.services import approval_service

        ctx_a = context_for(tenant_a)
        req = approval_service.create(
            session, ctx_a, kind="TEST_KIND", entity_type="x", entity_id=1, reason="test"
        )
        session.commit()

        resp = client_b.get(f"/api/v1/approvals/{req.id}")
        assert resp.status_code == 404

    def test_a_scheduled_report_from_another_shop_is_not_found(self, session, tenant_a, tenant_b, client_b):
        from app.models.enums import ReportSchedule
        from app.services import scheduled_report_service

        ctx_a = context_for(tenant_a)
        row = scheduled_report_service.create(
            session, ctx_a, report_type="sales_summary", schedule=ReportSchedule.DAILY
        )
        session.commit()

        resp = client_b.get(f"/api/v1/scheduled-reports/{row.id}")
        assert resp.status_code == 404

    def test_a_supplier_analytics_lookup_from_another_shop_is_not_found(
        self, session, tenant_a, tenant_b, client_b
    ):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()

        resp = client_b.get(f"/api/v1/intelligence/suppliers/{supplier.id}")
        assert resp.status_code == 404

    def test_a_customer_analytics_lookup_from_another_shop_is_not_found(
        self, session, tenant_a, tenant_b, client_b
    ):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        resp = client_b.get(f"/api/v1/intelligence/customers/{customer.id}")
        assert resp.status_code == 404

    def test_intelligence_dashboard_lists_are_scoped_to_the_callers_own_shop(
        self, session, tenant_a, tenant_b, client_a, client_b
    ):
        p_a = factories.make_product(
            session, tenant_a.shop, tenant_a.category, sku="A-ONLY", reorder_level=Decimal("100")
        )
        session.commit()
        from app.services import inventory_service

        ctx_a = context_for(tenant_a)
        inventory_service.record_opening_stock(session, ctx_a, product_id=p_a.id, quantity=Decimal(1))
        session.commit()

        resp_b = client_b.get("/api/v1/intelligence/reorder-recommendations")
        assert resp_b.status_code == 200
        body = resp_b.json()
        rows = body["items"] if isinstance(body, dict) and "items" in body else body
        assert all(row["product_id"] != p_a.id for row in rows)

    def test_a_stock_count_cannot_be_approved_or_posted_by_another_shops_client(
        self, session, tenant_a, tenant_b, client_b
    ):
        from app.services import stock_count_service

        ctx_a = context_for(tenant_a)
        factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        count = stock_count_service.create(session, ctx_a, title="A's count", scope=StockCountScope.FULL)
        session.commit()

        resp = client_b.post(f"/api/v1/stock-counts/{count.id}/approve", json={})
        assert resp.status_code == 404
