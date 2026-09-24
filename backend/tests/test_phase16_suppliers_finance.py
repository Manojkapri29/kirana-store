"""Supplier/purchase analytics and finance analytics: factual only, traceable to the existing services."""

from datetime import timedelta

from app.models import PurchaseItem
from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_purchase_return, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics"
P = {"preset": "this_month", "compare": "none"}


def _get(client, path, status=200, **params):
    r = client.get(f"{API}/{path}", params={**P, **params})
    assert r.status_code == status, r.text
    return r.json()


def _supplier(session, tenant, name):
    s = factories.make_supplier(session, tenant.shop, name=name)
    session.commit()
    return s


def _line(session, tenant, purchase, product, qty, cost):
    session.add(
        PurchaseItem(
            shop_id=tenant.shop.id,
            purchase_id=purchase.id,
            product_id=product.id,
            unit_id=product.unit_id,
            quantity=D(qty),
            unit_cost=D(cost),
            line_total=D(qty) * D(cost),
        )
    )
    session.flush()


class TestSuppliers:
    def test_spend_returns_share_and_no_invented_scores(self, session, tenant_a, client_a):
        a, b = _supplier(session, tenant_a, "Alpha"), _supplier(session, tenant_a, "Beta")
        p1 = make_purchase(session, tenant_a, a, "600.00", day=TODAY)
        make_purchase(session, tenant_a, b, "400.00", day=TODAY)
        make_purchase_return(session, tenant_a, p1, "100.00", day=TODAY)
        session.commit()
        t = _get(client_a, "suppliers/spend")
        rows = {r["supplier"]: r for r in t["rows"]}
        assert rows["Alpha"]["net_spend"] == "500.00" and rows["Alpha"]["purchase_returns"] == "100.00"
        assert rows["Alpha"]["share_pct"] == "55.56"
        assert any("no quality, reliability or delivery data" in n for n in t["notes"])
        assert not {"quality_score", "reliability", "delivery_score"} & set(rows["Alpha"])

    def test_concentration_is_a_fact_not_a_verdict(self, session, tenant_a, client_a):
        a, b = _supplier(session, tenant_a, "Alpha"), _supplier(session, tenant_a, "Beta")
        make_purchase(session, tenant_a, a, "750.00", day=TODAY)
        make_purchase(session, tenant_a, b, "250.00", day=TODAY)
        session.commit()
        o = _get(client_a, "suppliers/overview")
        assert (
            o["top_supplier"] == "Alpha"
            and o["top_supplier_share_pct"] == "75.00"
            and o["total_net_spend"] == "1000.00"
        )
        assert o["purchase_count"] == 2

    def test_product_cost_change_needs_two_purchases(self, session, tenant_a, client_a):
        a = _supplier(session, tenant_a, "Alpha")
        cat = factories.make_category(session, tenant_a.shop, name="Staples")
        rice = factories.make_product(session, tenant_a.shop, cat, sku="R", name="Rice")
        dal = factories.make_product(session, tenant_a.shop, cat, sku="D", name="Dal")
        early = make_purchase(
            session, tenant_a, a, "100.00", day=TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY
        )
        _line(session, tenant_a, early, rice, "10", "10.00")
        _line(session, tenant_a, early, dal, "1", "50.00")
        later = make_purchase(session, tenant_a, a, "120.00", day=TODAY)
        _line(session, tenant_a, later, rice, "10", "12.00")
        session.commit()
        rows = {r["product"]: r for r in _get(client_a, "suppliers/products")["rows"]}
        assert rows["Rice"]["latest_unit_cost"] == "12.00" and rows["Rice"]["average_unit_cost"] == "11.00"
        assert rows["Rice"]["cost_change_pct"] == "20.00"
        assert rows["Dal"]["cost_change_pct"] is None and "Insufficient Data" in rows["Dal"]["cost_note"]

    def test_cost_trend_needs_a_product(self, session, tenant_a, client_a):
        r = client_a.get(f"{API}/suppliers/cost-trend", params=P)
        assert r.status_code in (400, 422)

    def test_other_shops_suppliers_never_appear(self, session, tenant_a, tenant_b, client_b):
        a = _supplier(session, tenant_a, "Alpha")
        make_purchase(session, tenant_a, a, "999.00", day=TODAY)
        session.commit()
        assert _get(client_b, "suppliers/spend")["rows"] == []

    def test_needs_supplier_and_purchase_permission(self, session, tenant_a, make_client):
        c = client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "SUPPLIER_VIEW"])
        assert c.get(f"{API}/suppliers/spend", params=P).status_code == 403


class TestFinance:
    def test_summary_matches_the_pnl_and_never_fakes_profit(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        s = _get(client_a, "finance/summary")
        assert s["pnl"]["revenue"] == "1500.00"
        # Quick sales have no cost, so profit is not available rather than 1000-600 or a fabricated margin.
        assert s["pnl"]["gross_profit"] is None and s["profit_message"].startswith("Profit Not Available")

    def test_full_cost_gives_profit(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        s = _get(client_a, "finance/summary")
        assert s["pnl"]["gross_profit"] == "400.00" and s["profit_message"] is None

    def test_trend_and_payment_mix_and_aging(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        t = _get(client_a, "finance/trend", bucket="month")
        assert t["rows"] and t["rows"][0]["revenue"] == "1000.00"
        assert _get(client_a, "finance/payment-mix")["rows"]
        assert _get(client_a, "finance/receivables-aging")["title"].startswith("Receivables")
        assert _get(client_a, "finance/payables-aging")["title"].startswith("Payables")

    def test_expenses_and_tax_endpoints_respond(self, session, tenant_a, client_a):
        assert _get(client_a, "finance/expenses")["rows"] == []
        assert _get(client_a, "finance/expense-trend")["rows"] is not None
        tax = _get(client_a, "finance/tax")
        assert "status" in tax and tax["disclaimer"]

    def test_finance_analytics_need_finance_permission(self, session, tenant_a, make_client):
        c = client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "REPORT_VIEW"])
        assert c.get(f"{API}/finance/summary", params=P).status_code == 403

    def test_cross_shop_isolation(self, session, tenant_a, tenant_b, client_b):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        assert _get(client_b, "finance/summary")["pnl"]["revenue"] == "0.00"


class TestTrendCostIsBounded:
    def test_a_daily_trend_over_years_is_refused_not_run(self, session, tenant_a, client_a):
        r = client_a.get(
            f"{API}/finance/trend",
            params={"date_from": "2022-01-01", "date_to": "2026-01-01", "compare": "none", "bucket": "day"},
        )
        assert r.status_code == 422 and "too many" in r.text
        assert (
            client_a.get(
                f"{API}/finance/trend",
                params={
                    "date_from": "2022-01-01",
                    "date_to": "2026-01-01",
                    "compare": "none",
                    "bucket": "month",
                },
            ).status_code
            == 200
        )

    def test_the_builder_finance_dataset_picks_a_step_that_fits(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        session.commit()
        body = {"dataset": "finance", "definition": {"aggregations": [{"field": "revenue", "op": "SUM"}]}}
        wide = client_a.post(
            f"{API}/builder/preview",
            params={"date_from": "2022-01-01", "date_to": TODAY.isoformat(), "compare": "none"},
            json=body,
        )
        assert wide.status_code == 200, wide.text
        assert wide.json()["rows"][0]["sum_revenue"] == "100.00"
