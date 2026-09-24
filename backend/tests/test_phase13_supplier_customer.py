"""Supplier and customer analytics: price history from real posted purchases, no supplier ranked "best" without a
named metric, delivery-performance honesty, customer segmentation, and never a creditworthiness judgement."""

from decimal import Decimal

from app.services import customer_intelligence_service as cis
from app.services import khata_service
from app.services import supplier_intelligence_service as sis
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _post_purchase(client, supplier_id, product_id, quantity, unit_cost):
    resp = client.post(
        "/api/v1/purchases",
        json={
            "supplier_id": supplier_id,
            "items": [{"product_id": product_id, "quantity": str(quantity), "unit_cost": str(unit_cost)}],
        },
    )
    assert resp.status_code == 201, resp.text
    purchase_id = resp.json()["id"]
    resp = client.post(f"/api/v1/purchases/{purchase_id}/post")
    assert resp.status_code == 200, resp.text
    return purchase_id


class TestSupplierAnalytics:
    def test_delivery_performance_is_reported_as_unavailable_not_guessed(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()

        row = sis.analytics_for(session, tenant_a.shop.id, supplier.id)

        assert "unavailable" in row.delivery_performance_note.lower()

    def test_price_history_reflects_actual_posted_purchase_costs(self, session, tenant_a, client_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        _post_purchase(client_a, supplier.id, p.id, 10, "12.50")

        history = sis.price_history(session, tenant_a.shop.id, p.id, supplier.id)

        assert history.latest == Decimal("12.50")
        assert history.lowest == Decimal("12.50")
        assert len(history.points) == 1

    def test_no_purchases_yet_gives_an_honest_empty_row_not_a_fabricated_one(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()

        row = sis.analytics_for(session, tenant_a.shop.id, supplier.id)

        assert row.purchase_count == 0
        assert row.average_purchase_value is None


class TestCustomerAnalytics:
    def test_outstanding_balance_is_shown_as_a_plain_fact(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        ctx = context_for(tenant_a)
        khata_service.create_opening_balance(session, ctx, customer_id=customer.id, amount=Decimal("500"))
        session.commit()

        row = cis.analytics_for(session, tenant_a.shop.id, customer.id, TODAY)

        assert row.outstanding == Decimal("500")
        assert row.segments == [cis.CustomerSegment.CREDIT] or cis.CustomerSegment.CREDIT in row.segments

    def test_segmentation_thresholds_are_configurable_parameters(self, session, tenant_a):
        factories.make_customer(session, tenant_a.shop)
        session.commit()

        tight = cis.list_analytics(
            session, tenant_a.shop.id, TODAY, new_days=1, active_days=1, frequent_visits=100
        )
        loose = cis.list_analytics(
            session, tenant_a.shop.id, TODAY, new_days=365, active_days=365, frequent_visits=1
        )

        assert isinstance(tight, list)
        assert isinstance(loose, list)

    def test_a_customer_with_no_history_gets_an_honest_empty_row(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        row = cis.analytics_for(session, tenant_a.shop.id, customer.id, TODAY)

        assert row.total_purchases == Decimal("0")
        assert row.average_transaction_value is None
        assert row.segments == []
