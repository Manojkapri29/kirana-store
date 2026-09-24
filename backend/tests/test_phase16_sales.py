"""Advanced sales analytics: detailed vs quick vs online, product-level figures only from detailed bills, honest profit,
filters that are reported when they cannot apply, and pagination."""

from datetime import timedelta

from app.models.enums import PaymentMethod
from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_promotion_use, make_quick_sale, make_sale, make_sales_return

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics/sales"
P = {"preset": "this_month", "compare": "none"}


def _get(client, path, **params):
    r = client.get(f"{API}/{path}", params={**P, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _row(table, key, value):
    return next(r for r in table["rows"] if r[key] == value)


def _prod(session, tenant, name, sku, brand=None, category=None):

    cat = category or tenant.category
    p = factories.make_product(session, tenant.shop, cat, name=name, sku=sku, brand=brand)
    session.commit()
    return p


class TestQuickSalesStayMoneyOnly:
    def test_quick_sales_add_to_revenue_but_never_to_product_category_or_brand_figures(
        self, session, tenant_a, client_a
    ):
        rice = _prod(session, tenant_a, "Rice", "R1", brand="Acme")
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00", product=rice, qty=2)
        make_quick_sale(session, tenant_a, "900.00", day=TODAY)
        session.commit()

        summary = _get(client_a, "summary")
        assert (
            summary["detailed_revenue"] == "100.00"
            and summary["quick_revenue"] == "900.00"
            and summary["combined_revenue"] == "1000.00"
        )
        products = _get(client_a, "products")
        assert [r["product"] for r in products["rows"]] == ["Rice"] and products["rows"][0][
            "revenue"
        ] == "100.00"
        assert _get(client_a, "categories")["rows"][0]["revenue"] == "100.00"
        assert _get(client_a, "brands")["rows"][0]["revenue"] == "100.00"
        assert any("money-only" in n for n in products["notes"])

    def test_a_quick_sale_only_view_has_no_product_rows(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "50.00", day=TODAY)
        session.commit()
        assert _get(client_a, "products", channel="QUICK")["rows"] == []

    def test_no_online_figure_is_invented(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "10.00", day=TODAY, cogs="5.00")
        session.commit()
        ch = _get(client_a, "channels")
        online = _row(ch, "channel", "Online sales")
        assert online["revenue"] is None and online["transactions"] is None
        assert _get(client_a, "summary")["online_revenue"] is None


class TestChannelsAndTrend:
    def test_channels_add_up_to_combined_revenue_with_shares(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        make_quick_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()
        ch = _get(client_a, "channels")
        assert _row(ch, "channel", "Detailed sales")["share_pct"] == "75.00"
        assert _row(ch, "channel", "Combined revenue (before returns)")["revenue"] == "400.00"

    def test_daily_weekly_and_monthly_trends(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        make_sale(session, tenant_a, "20.00", day=TODAY, cogs="5.00")
        session.commit()
        day = _get(client_a, "trend", bucket="day")
        assert day["rows"][-1]["period"] == TODAY.isoformat() and day["rows"][-1]["combined_net"] == "30.00"
        assert day["rows"][-1]["average_transaction_value"] == "15.00"
        month = _get(client_a, "trend", bucket="month")
        assert month["rows"][-1]["period"] == TODAY.strftime("%Y-%m")
        week = _get(client_a, "trend", bucket="week")
        assert week["rows"][-1]["period"] == (TODAY - timedelta(days=TODAY.weekday())).isoformat()
        assert client_a.get(f"{API}/trend", params={**P, "bucket": "year"}).status_code in (400, 422)

    def test_the_trend_matches_the_existing_sales_report(self, session, tenant_a, client_a):
        from app.services import sales_report_service

        make_sale(session, tenant_a, "123.45", day=TODAY, cogs="1.00")
        make_quick_sale(session, tenant_a, "76.55", day=TODAY)
        session.commit()
        existing = sales_report_service.sales_summary(session, tenant_a.shop.id, TODAY.replace(day=1), TODAY)
        rows = _get(client_a, "trend", bucket="month")["rows"]
        assert D(rows[-1]["combined_net"]) == existing.combined.net

    def test_period_boundaries_are_inclusive(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "1.00", day=TODAY - timedelta(days=1))
        make_quick_sale(session, tenant_a, "2.00", day=TODAY)
        session.commit()
        t = _get(
            client_a,
            "trend",
            preset="custom",
            date_from=(TODAY - timedelta(days=1)).isoformat(),
            date_to=TODAY.isoformat(),
        )
        assert [r["combined_net"] for r in t["rows"]] == ["1.00", "2.00"]
        only = _get(
            client_a, "trend", preset="custom", date_from=TODAY.isoformat(), date_to=TODAY.isoformat()
        )
        assert [r["combined_net"] for r in only["rows"]] == ["2.00"]


class TestProducts:
    def test_top_and_bottom_selling_products_are_ordered_and_stable(self, session, tenant_a, client_a):
        a, b, c = (
            _prod(session, tenant_a, n, s) for n, s in (("Apple", "A1"), ("Banana", "B1"), ("Cherry", "C1"))
        )
        make_sale(session, tenant_a, "50.00", day=TODAY, cogs="20.00", product=a, qty=5)
        make_sale(session, tenant_a, "10.00", day=TODAY, cogs="4.00", product=b, qty=1)
        make_sale(session, tenant_a, "30.00", day=TODAY, cogs="9.00", product=c, qty=3)
        session.commit()
        assert [r["product"] for r in _get(client_a, "products")["rows"]] == ["Apple", "Cherry", "Banana"]
        assert [r["product"] for r in _get(client_a, "products", order="bottom")["rows"]] == [
            "Banana",
            "Cherry",
            "Apple",
        ]

    def test_returns_reduce_product_quantity_revenue_and_profit(self, session, tenant_a, client_a):
        p = _prod(session, tenant_a, "Soap", "S1")
        sale = make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00", product=p, qty=10)
        make_sales_return(session, tenant_a, sale, "20.00", day=TODAY, cogs="12.00")
        session.commit()
        row = _get(client_a, "products")["rows"][0]
        assert (row["revenue"], row["cogs"], row["gross_profit"]) == ("80.00", "48.00", "32.00")

    def test_a_missing_cost_gives_no_profit_never_a_fake_one(self, session, tenant_a, client_a):
        p = _prod(session, tenant_a, "Mystery", "M1")
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs=None, product=p)
        session.commit()
        row = _get(client_a, "products")["rows"][0]
        assert (
            row["gross_profit"] is None
            and row["cogs"] is None
            and row["profit_note"] == "Insufficient Cost Data"
        )
        assert _get(client_a, "categories")["rows"][0]["gross_profit"] is None

    def test_offers_reduce_product_revenue(self, session, tenant_a, client_a):
        p = _prod(session, tenant_a, "Tea", "T1")
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="40.00", product=p, promo_discount="10.00")
        session.commit()
        assert _get(client_a, "products")["rows"][0]["revenue"] == "90.00"

    def test_filters_by_product_category_and_brand_and_ignored_ones_are_reported(
        self, session, tenant_a, client_a
    ):
        a = _prod(session, tenant_a, "Apple", "A1", brand="Acme")
        b = _prod(session, tenant_a, "Banana", "B1", brand="Zed")
        make_sale(session, tenant_a, "10.00", day=TODAY, cogs="1.00", product=a)
        make_sale(session, tenant_a, "20.00", day=TODAY, cogs="1.00", product=b)
        session.commit()
        assert [r["product"] for r in _get(client_a, "products", brand="Zed")["rows"]] == ["Banana"]
        assert [r["product"] for r in _get(client_a, "products", product_id=a.id)["rows"]] == ["Apple"]
        assert len(_get(client_a, "products", category_id=tenant_a.category.id)["rows"]) == 2
        ignored = _get(client_a, "products", supplier_id=5)
        assert ignored["filters_ignored"] == ["supplier_id"] and any(
            "supplier_id" in n for n in ignored["notes"]
        )

    def test_customer_and_payment_method_filters_apply(self, session, tenant_a, client_a):
        c = factories.make_customer(session, tenant_a.shop)
        p = _prod(session, tenant_a, "Apple", "A1")
        session.commit()
        make_sale(
            session,
            tenant_a,
            "10.00",
            day=TODAY,
            cogs="1.00",
            product=p,
            customer_id=c.id,
            method=PaymentMethod.UPI,
        )
        make_sale(session, tenant_a, "90.00", day=TODAY, cogs="1.00", product=p)
        session.commit()
        assert _get(client_a, "products", customer_id=c.id)["rows"][0]["revenue"] == "10.00"
        assert _get(client_a, "products", payment_method="UPI")["rows"][0]["revenue"] == "10.00"
        assert _get(client_a, "trend", customer_id=c.id)["rows"][0]["combined_net"] == "10.00"

    def test_pagination_caps_the_page_and_reports_the_total(self, session, tenant_a, client_a):
        for i in range(7):
            p = _prod(session, tenant_a, f"P{i}", f"K{i}")
            make_sale(session, tenant_a, f"{10 + i}.00", day=TODAY, cogs="1.00", product=p)
        session.commit()
        page = _get(client_a, "products", limit=3, offset=0)
        assert page["total"] == 7 and len(page["rows"]) == 3
        assert len(_get(client_a, "products", limit=3, offset=6)["rows"]) == 1
        assert client_a.get(f"{API}/products", params={**P, "limit": 1000}).status_code == 422


class TestPaymentsDiscountsPromotions:
    def test_payment_methods_split_received_from_credit(self, session, tenant_a, client_a):
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        make_sale(
            session,
            tenant_a,
            "500.00",
            day=TODAY,
            cogs="1.00",
            paid="200.00",
            customer_id=c.id,
            method=PaymentMethod.CASH,
        )
        session.commit()
        t = _get(client_a, "payment-methods")
        assert (
            _row(t, "method", "UPI")["received"] == "100.00"
            and _row(t, "method", "CASH")["received"] == "200.00"
        )
        assert _row(t, "method", "On credit (not yet received)")["received"] == "300.00"

    def test_discount_analysis_separates_line_bill_offer_and_quick_discounts(
        self, session, tenant_a, client_a
    ):
        sale = make_sale(session, tenant_a, "90.00", day=TODAY, cogs="1.00")
        sale.subtotal, sale.discount, sale.promotion_discount = D("100.00"), D("5.00"), D("5.00")
        session.commit()
        t = _get(client_a, "discounts")
        assert _row(t, "kind", "Cashier bill discounts (detailed)")["amount"] == "5.00"
        assert _row(t, "kind", "Offers and coupons (detailed)")["amount"] == "5.00"

    def test_promotion_linked_sales_are_associated_never_caused(self, session, tenant_a, client_a):
        used = make_sale(session, tenant_a, "80.00", day=TODAY, cogs="1.00")
        make_sale(session, tenant_a, "20.00", day=TODAY, cogs="1.00")
        make_promotion_use(session, tenant_a, used, "Diwali", "5.00")
        session.commit()
        t = _get(client_a, "promotions")
        assert _row(t, "promotion", "Diwali")["bill_revenue"] == "80.00"
        assert _row(t, "promotion", "All detailed bills")["bill_revenue"] == "100.00"
        assert any("not revenue caused by the promotion" in n for n in t["notes"])


class TestSummaryFigures:
    def test_atv_units_per_transaction_and_return_rate(self, session, tenant_a, client_a):
        sale = make_sale(session, tenant_a, "600.00", day=TODAY, cogs="1.00", qty=6)
        make_sale(session, tenant_a, "200.00", day=TODAY, cogs="1.00", qty=2)
        make_quick_sale(session, tenant_a, "200.00", day=TODAY)
        make_sales_return(session, tenant_a, sale, "100.00", day=TODAY, cogs="1.00")
        session.commit()
        s = _get(client_a, "summary")
        assert s["transactions"] == 3 and s["average_transaction_value"] == "333.33"
        assert s["units_per_transaction"] == "4.00" and s["return_rate_pct"] == "10.00"
        assert s["net_revenue_after_returns"] == "900.00"

    def test_an_empty_period_has_no_average_not_a_zero_average(self, session, tenant_a, client_a):
        s = _get(client_a, "summary")
        assert (
            s["average_transaction_value"] is None
            and s["units_per_transaction"] is None
            and s["return_rate_pct"] is None
        )


class TestAccessAndIsolation:
    def test_draft_and_voided_bills_never_appear(self, session, tenant_a, client_a):
        from app.models.enums import SaleStatus

        s = make_sale(session, tenant_a, "999.00", day=TODAY, cogs="1.00")
        s.status = SaleStatus.VOID
        s.void_reason = "x"
        session.commit()
        assert (
            _get(client_a, "summary")["combined_revenue"] == "0.00"
            and _get(client_a, "products")["rows"] == []
        )

    def test_permissions_and_shop_isolation(self, session, tenant_a, tenant_b, client_b, make_client):
        make_sale(session, tenant_a, "500.00", day=TODAY, cogs="1.00")
        session.commit()
        assert _get(client_b, "summary")["combined_revenue"] == "0.00"
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_VIEW"]).get(f"{API}/summary").status_code == 403
        )  # needs REPORT_VIEW too
        assert client_with(make_client, tenant_a, ["REPORT_VIEW"]).get(f"{API}/summary").status_code == 403
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_VIEW", "REPORT_VIEW"])
            .get(f"{API}/summary")
            .status_code
            == 200
        )


class TestExistingProductRevenueRegression:
    """Found by the Phase 16 audit: `analytics_service.product_sales` summed `line_total - promotion_discount` in SQL, which has
    no type of its own, so on SQLite it came back as raw paise and the revenue was reported as 0.00. Both the assistant's product
    tools and the fast/slow-mover figures use it."""

    def test_product_sales_revenue_is_the_real_amount_not_zero(self, session, tenant_a):
        from app.services import analytics_service

        p = _prod(session, tenant_a, "Rice", "R1")
        make_sale(
            session, tenant_a, "100.00", day=TODAY, cogs="40.00", product=p, qty=4, promo_discount="10.00"
        )
        session.commit()
        row = analytics_service.product_sales(session, tenant_a.shop.id, TODAY, TODAY)[0]
        assert (row.quantity, row.revenue) == (D("4.000"), D("90.00"))
