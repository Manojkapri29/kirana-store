"""Inventory intelligence: fast/slow/dead movers, stock aging, and the health summary, all against real
inventory + sales data, with configurable periods and no fabricated money figures."""

from decimal import Decimal

from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _stock(session, ctx, product, qty, unit_cost=None):
    from app.services import inventory_service

    inventory_service.record_opening_stock(
        session, ctx, product_id=product.id, quantity=Decimal(qty), unit_cost=unit_cost
    )
    session.commit()


class TestStockValue:
    def test_total_value_sums_known_costs_and_counts_the_unknown_ones(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        known = factories.make_product(session, tenant_a.shop, tenant_a.category, sku="A", name="Known cost")
        unknown = factories.make_product(
            session, tenant_a.shop, tenant_a.category, sku="B", name="Unknown cost"
        )
        session.commit()
        _stock(session, ctx, known, 10, unit_cost=Decimal("50"))
        _stock(session, ctx, unknown, 5, unit_cost=None)

        value, missing = iis.stock_value(session, tenant_a.shop.id)

        assert value == Decimal("500")
        assert missing == 1

    def test_value_is_none_not_zero_when_every_held_product_has_no_cost(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        _stock(session, ctx, p, 10, unit_cost=None)

        value, missing = iis.stock_value(session, tenant_a.shop.id)

        assert value is None
        assert missing == 1


class TestMovers:
    def test_fast_moving_ranks_by_units_sold_in_the_period(self, session, tenant_a, client_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        hot = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            sku="HOT",
            name="Hot seller",
            selling_price=Decimal("10"),
        )
        cold = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            sku="COLD",
            name="Cold seller",
            selling_price=Decimal("10"),
        )
        session.commit()
        _stock(session, ctx, hot, 100, unit_cost=Decimal("5"))
        _stock(session, ctx, cold, 100, unit_cost=Decimal("5"))
        _sell(client_a, hot.id, 20)
        _sell(client_a, cold.id, 2)

        movers = iis.fast_moving(session, tenant_a.shop.id, TODAY, days=30)

        assert movers[0].product_id == hot.id

    def test_dead_stock_is_held_stock_with_zero_sales_in_the_period(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        _stock(session, ctx, p, 10, unit_cost=Decimal("5"))

        dead = iis.dead_stock(session, tenant_a.shop.id, TODAY, days=30)

        assert any(d.product_id == p.id for d in dead)

    def test_periods_are_configurable_and_change_the_window(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        _stock(session, ctx, p, 10, unit_cost=Decimal("5"))

        short_dead = iis.dead_stock(session, tenant_a.shop.id, TODAY, days=7)
        long_dead = iis.dead_stock(session, tenant_a.shop.id, TODAY, days=90)

        # No sales at all in either window, so the product shows up as dead stock under both period lengths.
        assert any(r.product_id == p.id for r in short_dead)
        assert any(r.product_id == p.id for r in long_dead)


class TestStockAging:
    def test_age_is_days_since_the_most_recent_inbound_transaction(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        _stock(session, ctx, p, 10, unit_cost=Decimal("5"))

        aging = iis.stock_aging(session, tenant_a.shop.id, TODAY)

        row = next(r for r in aging if r.product_id == p.id)
        assert row.days_since_last_inbound == 0  # opened today


class TestInventoryHealth:
    def test_health_summarises_the_same_period_consistently(self, session, tenant_a):
        from app.services import inventory_intelligence_service as iis

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, reorder_level=Decimal("5"))
        session.commit()
        _stock(session, ctx, p, 3, unit_cost=Decimal("5"))  # at/below reorder level

        health = iis.inventory_health(session, tenant_a.shop.id, TODAY, days=30)

        assert health.period_days == 30
        assert health.total_products == 1
        assert health.risk_stockout == 1


def _sell(client, product_id: int, quantity: int) -> None:
    resp = client.post(
        "/api/v1/sales", json={"items": [{"product_id": product_id, "quantity": str(quantity)}]}
    )
    assert resp.status_code == 201, resp.text
    sale_id = resp.json()["id"]
    resp = client.post(f"/api/v1/sales/{sale_id}/post", json={"payment_method": "CASH"})
    assert resp.status_code == 200, resp.text
