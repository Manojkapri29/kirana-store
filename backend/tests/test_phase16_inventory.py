"""Advanced inventory analytics: the inventory ledger is the only source of stock; cost is the average cost; quick sales never
appear; nothing is fabricated."""

from datetime import timedelta
from decimal import Decimal

from app.models.enums import AdjustmentReason
from app.services import inventory_service
from tests import factories
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics/inventory"
P = {"preset": "this_month", "compare": "none"}


def _get(client, path, **params):
    r = client.get(f"{API}/{path}", params={**P, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _stocked(session, tenant, name, sku, qty, cost="10.00", day=None, category=None):
    p = factories.make_product(session, tenant.shop, category or tenant.category, name=name, sku=sku)
    session.commit()
    inventory_service.record_opening_stock(
        session,
        context_for(tenant),
        product_id=p.id,
        quantity=Decimal(qty),
        unit_cost=D(cost) if cost else None,
        txn_date=day,
    )
    session.commit()
    return p


def _row(table, name):
    return next(r for r in table["rows"] if r["product"] == name)


class TestStockAndValue:
    def test_current_stock_and_value_come_from_the_ledger_and_average_cost(self, session, tenant_a, client_a):
        _stocked(session, tenant_a, "Rice", "R1", "10", "20.00")
        _stocked(session, tenant_a, "Mystery", "M1", "5", None)
        t = _get(client_a, "stock")
        assert _row(t, "Rice")["stock_value"] == "200.00" and _row(t, "Rice")["current_stock"] == "10.000"
        assert _row(t, "Mystery")["stock_value"] is None  # unknown cost: never zero
        assert any("no known cost" in n for n in t["notes"])

    def test_category_summary_leaves_out_unknown_cost_and_says_so(self, session, tenant_a, client_a):
        _stocked(session, tenant_a, "Rice", "R1", "10", "20.00")
        _stocked(session, tenant_a, "Mystery", "M1", "5", None)
        row = _get(client_a, "categories")["rows"][0]
        assert (
            row["stock_value"] == "200.00"
            and row["unknown_cost"] == 1
            and "unknown cost" in row["value_note"]
        )

    def test_filters_narrow_the_stock_and_unsupported_ones_are_reported(self, session, tenant_a, client_a):
        a = _stocked(session, tenant_a, "Rice", "R1", "10")
        _stocked(session, tenant_a, "Dal", "D1", "3")
        t = _get(client_a, "stock", product_id=a.id, customer_id=4)
        assert [r["product"] for r in t["rows"]] == ["Rice"] and t["filters_ignored"] == ["customer_id"]
        assert client_a.get(f"{API}/stock", params={**P, "active": "false"}).json()["rows"] == []


class TestMovementAndBalance:
    def test_movement_purchase_vs_sales_and_quick_sales_never_move_stock(self, session, tenant_a, client_a):
        p = _stocked(session, tenant_a, "Rice", "R1", "10")
        ctx = context_for(tenant_a)
        inventory_service.record_adjustment(
            session, ctx, product_id=p.id, quantity_delta=Decimal("-2"), reason_code=AdjustmentReason.DAMAGED
        )
        make_quick_sale(session, tenant_a, "999.00", day=TODAY)
        session.commit()
        mv = _get(client_a, "movement")
        types = {r["txn_type"]: r for r in mv["rows"]}
        assert (
            types["OPENING"]["quantity_in"] == "10.000"
            and types["ADJUSTMENT"]["quantity_out"] == "2.000"
            and "SALE" not in types
        )
        assert _get(client_a, "stock")["rows"][0]["current_stock"] == "8.000"

    def test_adjustment_trend_groups_by_reason_and_month(self, session, tenant_a, client_a):
        p = _stocked(session, tenant_a, "Rice", "R1", "10")
        inventory_service.record_adjustment(
            session,
            context_for(tenant_a),
            product_id=p.id,
            quantity_delta=Decimal("-3"),
            reason_code=AdjustmentReason.EXPIRED,
        )
        session.commit()
        row = _get(client_a, "adjustments")["rows"][0]
        assert (row["reason"], row["adjustments"], row["quantity_out"]) == ("EXPIRED", 1, "3.000") and row[
            "period"
        ] == TODAY.strftime("%Y-%m")

    def test_stock_out_history_is_rebuilt_from_the_running_balance(self, session, tenant_a, client_a):
        p = _stocked(session, tenant_a, "Rice", "R1", "5", day=TODAY - timedelta(days=5))
        inventory_service.record_adjustment(
            session,
            context_for(tenant_a),
            product_id=p.id,
            quantity_delta=Decimal("-5"),
            reason_code=AdjustmentReason.DAMAGED,
            txn_date=TODAY - timedelta(days=3),
        )
        inventory_service.record_adjustment(
            session,
            context_for(tenant_a),
            product_id=p.id,
            quantity_delta=Decimal("4"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
            txn_date=TODAY - timedelta(days=1),
        )
        session.commit()
        t = _get(
            client_a,
            "stock-outs",
            preset="custom",
            date_from=(TODAY - timedelta(days=6)).isoformat(),
            date_to=TODAY.isoformat(),
        )
        row = _row(t, "Rice")
        assert (
            row["first_stock_out"] == (TODAY - timedelta(days=3)).isoformat()
            and row["days_out_of_stock"] == 2
            and row["still_out"] is False
        )


class TestTurnoverAndMovers:
    def test_turnover_is_units_sold_over_average_stock(self, session, tenant_a, client_a):
        p = _stocked(session, tenant_a, "Rice", "R1", "10", day=TODAY - timedelta(days=40))
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="50.00", product=p, qty=5)
        session.commit()
        ctx = context_for(tenant_a)
        inventory_service.record_adjustment(
            session,
            ctx,
            product_id=p.id,
            quantity_delta=Decimal("-5"),
            reason_code=AdjustmentReason.DAMAGED,
            txn_date=TODAY,
        )
        session.commit()
        row = _row(_get(client_a, "turnover"), "Rice")
        # opening 10 units, closing 10 - 5 (damaged) = 5 (the test data wrote no ledger row for the sale): average 7.5
        assert row["opening_stock"] == "10.000" and row["units_sold"] == "5.000" and row["turnover"] == "0.67"

    def test_no_stock_and_no_sales_gives_no_row_and_insufficient_data_never_infinite(
        self, session, tenant_a, client_a
    ):
        p = _stocked(session, tenant_a, "Rice", "R1", "10", day=TODAY - timedelta(days=40))
        inventory_service.record_adjustment(
            session,
            context_for(tenant_a),
            product_id=p.id,
            quantity_delta=Decimal("-10"),
            reason_code=AdjustmentReason.DAMAGED,
            txn_date=TODAY - timedelta(days=39),
        )
        session.commit()
        assert _get(client_a, "turnover")["rows"] == []

    def test_fast_slow_and_dead_use_the_intelligence_service_definitions(self, session, tenant_a, client_a):
        fast = _stocked(session, tenant_a, "Fast", "F1", "100")
        _stocked(session, tenant_a, "Dead", "D1", "50")
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="10.00", product=fast, qty=10)
        session.commit()
        assert [r["product"] for r in _get(client_a, "movers", kind="fast")["rows"]] == ["Fast"]
        assert [r["product"] for r in _get(client_a, "movers", kind="dead")["rows"]] == ["Dead"]
        assert client_a.get(f"{API}/movers", params={**P, "kind": "bogus"}).status_code in (400, 422)


class TestOtherViews:
    def test_purchase_vs_sales_and_reorder_and_aging_and_count_variance_respond(
        self, session, tenant_a, client_a
    ):
        _stocked(session, tenant_a, "Rice", "R1", "1")
        for path in ("purchase-vs-sales", "reorder", "aging", "count-variance", "summary", "turnover"):
            assert client_a.get(f"{API}/{path}", params=P).status_code == 200, path
        assert (
            _get(client_a, "purchase-vs-sales")["rows"] == []
        )  # an opening balance is neither a purchase nor a sale

    def test_aging_and_reorder_list_the_stocked_product(self, session, tenant_a, client_a):
        _stocked(session, tenant_a, "Rice", "R1", "1", day=TODAY - timedelta(days=30))
        assert _row(_get(client_a, "aging"), "Rice")["days_since_last_inbound"] == 30


class TestAccessAndIsolation:
    def test_needs_advanced_analytics_and_inventory_view(self, session, tenant_a, make_client):
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_VIEW", "INVENTORY_VIEW"])
            .get(f"{API}/stock")
            .status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED"]).get(f"{API}/stock").status_code == 403
        )
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "INVENTORY_VIEW"])
            .get(f"{API}/stock")
            .status_code
            == 200
        )

    def test_another_shops_stock_never_appears(self, session, tenant_a, tenant_b, client_b):
        _stocked(session, tenant_a, "Rice", "R1", "10")
        assert _get(client_b, "stock")["rows"] == [] and _get(client_b, "movement")["rows"] == []

    def test_pagination(self, session, tenant_a, client_a):
        for i in range(5):
            _stocked(session, tenant_a, f"P{i}", f"K{i}", "1")
        page = _get(client_a, "stock", limit=2, offset=4)
        assert page["total"] == 5 and len(page["rows"]) == 1
