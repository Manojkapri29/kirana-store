"""Sales reports: Detailed / Quick / Combined, Gross - Discount = Net, discount analytics, plan, isolation."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import UserRole
from tests.test_promotions import SALES, live, sold, state
from tests.test_promotions import shop as shop  # noqa: F401  (the shared fixture)
from tests.test_sales_api import item, make_customer

D = Decimal
API = "/api/v1/reports"
QUICK = "/api/v1/quick-sales"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


def summary(client, **params):
    response = client.get(f"{API}/sales-summary", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def quick(client, gross, day, **extra):
    made = client.post(QUICK, json={"gross_amount": gross, "sale_date": day, **extra}).json()
    response = client.post(f"{QUICK}/{made['id']}/post", json={"payment_method": "CASH"})
    assert response.status_code == 200, response.text
    return response.json()


def balanced(t):
    assert D(t["gross_sales"]) - D(t["discount"]) == D(t["net_sales"]), t
    assert D(t["line_discount"]) + D(t["bill_discount"]) + D(t["promotion_discount"]) == D(t["discount"])


@pytest.fixture
def busy(client_a, shop):
    """Detailed: 2 rice + 1 sugar on 10 Feb (line discount 10, bill discount 5, 10% offer); rice on 12 Feb.
    Quick: 1000 less 100 on 10 Feb, 500 on 11 Feb."""
    made = live(client_a, name="Festival", percent="10", coupon_code="FEST")
    a = sold(client_a, [item(shop["rice"], "2", discount="10"), item(shop["sugar"], "1")], header={"sale_date": "2026-02-10", "discount": "5", "coupon_code": "FEST"})  # fmt: skip
    state(client_a, made, "pause")
    b = sold(client_a, [item(shop["rice"], "1")], header={"sale_date": "2026-02-12"})
    quick(client_a, "1000", "2026-02-10", discount="100")
    quick(client_a, "500", "2026-02-11")
    return a, b


class TestSummary:
    def test_detailed_quick_and_combined_are_separate_and_balance(self, client_a, busy):
        s = summary(client_a, date_from="2026-02-01", date_to="2026-02-28")
        d, q, c = s["detailed"], s["quick"], s["combined"]
        # sale a: gross 100+48 = 148, line total 90+48 = 138, less bill 5 and 10% offer 13.80 = 119.20. sale b: 50
        assert (d["sales_count"], d["gross_sales"], d["line_discount"], d["bill_discount"], d["promotion_discount"], d["net_sales"]) == (2, "198.00", "10.00", "5.00", "13.80", "169.20")  # fmt: skip
        assert (q["sales_count"], q["gross_sales"], q["line_discount"], q["bill_discount"], q["promotion_discount"], q["net_sales"]) == (2, "1500.00", "0.00", "100.00", "0.00", "1400.00")  # fmt: skip
        assert (c["sales_count"], c["gross_sales"], c["discount"], c["net_sales"]) == (
            4,
            "1698.00",
            "128.80",
            "1569.20",
        )
        for t in (d, q, c):
            balanced(t)

    def test_each_day_shows_both_kinds(self, client_a, busy):
        days = {
            row["day"]: row for row in summary(client_a, date_from="2026-02-01", date_to="2026-02-28")["days"]
        }
        assert sorted(days) == ["2026-02-10", "2026-02-11", "2026-02-12"]
        ten = days["2026-02-10"]
        assert (ten["detailed"]["net_sales"], ten["quick"]["net_sales"], ten["combined"]["net_sales"]) == (
            "119.20",
            "900.00",
            "1019.20",
        )
        assert (
            days["2026-02-11"]["detailed"]["sales_count"] == 0
            and days["2026-02-11"]["quick"]["net_sales"] == "500.00"
        )
        assert days["2026-02-12"]["quick"]["sales_count"] == 0
        for row in days.values():
            for key in ("detailed", "quick", "combined"):
                balanced(row[key])

    def test_profit_is_only_for_detailed_sales_and_the_combined_figure_says_not_available(
        self, client_a, busy
    ):
        s = summary(client_a, date_from="2026-02-01", date_to="2026-02-28")
        assert s["combined_profit"] is None and s["combined_profit_label"] == "Not Available"
        # costs: rice 20, sugar 20; net 169.20 less cost (3 rice x 20 + 1 sugar x 20 = 80)
        assert s["detailed_gross_profit"] == "89.20" and s["detailed_sales_without_cost"] == 0

    def test_a_sale_with_an_unknown_cost_is_left_out_of_profit_and_counted(self, client_a, shop):
        sold(client_a, [item(shop["oil"], "1")], header={"sale_date": "2026-03-01"})  # oil has no known cost
        sold(client_a, [item(shop["rice"], "1")], header={"sale_date": "2026-03-01"})
        s = summary(client_a, date_from="2026-03-01", date_to="2026-03-31")
        assert s["detailed_gross_profit"] == "30.00" and s["detailed_sales_without_cost"] == 1
        assert s["detailed"]["net_sales"] == "190.00"  # revenue still counts the oil sale

    def test_a_quick_sale_never_adds_to_profit(self, client_a):
        quick(client_a, "9999", "2026-04-01")
        s = summary(client_a, date_from="2026-04-01", date_to="2026-04-30")
        assert s["detailed_gross_profit"] is None and s["quick"]["net_sales"] == "9999.00"

    def test_only_posted_sales_count(self, client_a, shop):
        first = sold(client_a, [item(shop["rice"], "2")], header={"sale_date": "2026-05-01"})
        client_a.post(f"{SALES}/{first['id']}/void", json={"reason": "x"})
        client_a.post(SALES, json={"items": [item(shop["rice"])], "sale_date": "2026-05-01"})  # a draft
        made = client_a.post(QUICK, json={"gross_amount": "300", "sale_date": "2026-05-01"}).json()  # a draft
        posted = quick(client_a, "200", "2026-05-01")
        client_a.post(f"{QUICK}/{posted['id']}/void", json={"reason": "x"})
        s = summary(client_a, date_from="2026-05-01", date_to="2026-05-31")
        assert (
            s["combined"]["sales_count"] == 0
            and s["combined"]["net_sales"] == "0.00"
            and made["status"] == "DRAFT"
        )
        assert s["days"] == []

    def test_the_date_range_is_inclusive_and_defaults_to_the_month_so_far(self, client_a, shop):
        sold(client_a, [item(shop["rice"])], header={"sale_date": "2026-06-01"})
        sold(client_a, [item(shop["rice"])], header={"sale_date": "2026-06-30"})
        assert summary(client_a, date_from="2026-06-01", date_to="2026-06-30")["detailed"]["sales_count"] == 2
        assert summary(client_a, date_from="2026-06-02", date_to="2026-06-29")["detailed"]["sales_count"] == 0
        assert summary(client_a, date_from="2026-06-30", date_to="2026-06-30")["detailed"]["sales_count"] == 1
        assert client_a.get(f"{API}/sales-summary").status_code == 200
        assert (
            client_a.get(
                f"{API}/sales-summary", params={"date_from": "2026-07-02", "date_to": "2026-07-01"}
            ).status_code
            == 422
        )
        assert client_a.get(f"{API}/sales-summary", params={"date_from": "nope"}).status_code == 422

    def test_it_is_open_to_every_plan_and_every_role(self, client_a, tenant_a, make_client, give_plan, busy):
        give_plan(tenant_a, "free")
        assert summary(client_a, date_from="2026-02-01", date_to="2026-02-28")["combined"]["sales_count"] == 4
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert staff.get(f"{API}/sales-summary").status_code == 200

    def test_shops_never_see_each_others_sales(self, client_a, client_b, busy):
        s = summary(client_b, date_from="2026-02-01", date_to="2026-02-28")
        assert s["combined"]["sales_count"] == 0 and s["days"] == []


class TestDiscountReport:
    def test_totals_promotions_and_coupons(self, client_a, busy):
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
        ).json()
        assert (r["gross_sales"], r["net_sales"], r["total_discount"]) == ("1698.00", "1569.20", "128.80")
        assert (r["line_discount"], r["bill_discount"], r["promotion_discount"]) == (
            "10.00",
            "105.00",
            "13.80",
        )
        assert (
            r["promotions_used"],
            r["promotion_applications"],
            r["coupon_uses"],
            r["coupon_discount"],
        ) == (1, 1, 1, "13.80")
        assert r["by_promotion"] == [
            {
                "promotion_id": r["by_promotion"][0]["promotion_id"],
                "name": "Festival",
                "uses": 1,
                "discount": "13.80",
            }
        ]
        assert r["by_coupon"] == [{"code": "FEST", "uses": 1, "discount": "13.80"}]
        assert D(r["gross_sales"]) - D(r["total_discount"]) == D(r["net_sales"])

    def test_by_date_lists_only_days_with_discounts(self, client_a, busy):
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
        ).json()
        assert [d["day"] for d in r["by_date"]] == ["2026-02-10"]
        day = r["by_date"][0]
        assert (day["line_discount"], day["bill_discount"], day["promotion_discount"], day["total"]) == (
            "10.00",
            "105.00",
            "13.80",
            "128.80",
        )

    def test_a_voided_sale_gives_its_discount_and_use_back(self, client_a, shop):
        live(client_a, name="Ten", percent="10")
        a = sold(client_a, [item(shop["rice"], "2")], header={"sale_date": "2026-03-05"})
        sold(client_a, [item(shop["rice"], "2")], header={"sale_date": "2026-03-06"})
        client_a.post(f"{SALES}/{a['id']}/void", json={"reason": "x"})
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
        ).json()
        assert (r["promotion_discount"], r["promotion_applications"]) == ("10.00", 1)
        assert r["by_promotion"][0]["uses"] == 1 and [d["day"] for d in r["by_date"]] == ["2026-03-06"]

    def test_history_is_read_from_the_snapshots_not_the_current_offer(self, client_a, shop):
        made = live(client_a, name="Old Name", percent="10")
        sold(client_a, [item(shop["rice"], "2")], header={"sale_date": "2026-03-05"})
        client_a.patch(f"/api/v1/promotions/{made['id']}", json={"name": "New Name", "percent": "50"})
        state(client_a, made, "expire")
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
        ).json()
        assert r["by_promotion"][0]["name"] == "Old Name" and r["promotion_discount"] == "10.00"

    def test_the_most_used_or_costly_offer_comes_first(self, client_a, shop):
        live(client_a, name="Small", percent="10", stackable=True, priority=2)
        live(
            client_a,
            name="Bill",
            promo_type="AMOUNT",
            percent=None,
            amount="30",
            stackable=True,
            priority=1,
            min_cart_value="150",
        )
        sold(client_a, [item(shop["rice"], "4")], header={"sale_date": "2026-03-05"})  # 200: 20 + 30
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
        ).json()
        assert [p["name"] for p in r["by_promotion"]] == ["Bill", "Small"] and r["promotions_used"] == 2

    def test_it_needs_the_plans_advanced_reports(self, client_a, tenant_a, give_plan):
        give_plan(tenant_a, "free")
        refused = client_a.get(f"{API}/discounts")
        assert refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "advanced_reports"
        give_plan(tenant_a, "basic")
        assert client_a.get(f"{API}/discounts").status_code == 200

    def test_shops_never_see_each_others_discounts(self, client_a, client_b, busy):
        r = client_b.get(
            f"{API}/discounts", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
        ).json()
        assert r["total_discount"] == "0.00" and r["by_promotion"] == [] and r["by_coupon"] == []

    def test_an_empty_period(self, client_a):
        r = client_a.get(
            f"{API}/discounts", params={"date_from": "2020-01-01", "date_to": "2020-01-31"}
        ).json()
        assert (r["gross_sales"], r["total_discount"], r["promotions_used"], r["by_date"]) == (
            "0.00",
            "0.00",
            0,
            [],
        )


class TestConsistency:
    def test_the_report_agrees_with_the_invoices(self, client_a, shop, busy):
        a, b = busy
        s = summary(client_a, date_from="2026-02-01", date_to="2026-02-28")
        assert D(s["detailed"]["net_sales"]) == D(a["total_amount"]) + D(b["total_amount"])
        assert D(s["detailed"]["promotion_discount"]) == D(a["promotion_discount"]) + D(
            b["promotion_discount"]
        )
        assert D(s["detailed"]["bill_discount"]) == D(a["discount"]) + D(b["discount"])
        assert D(s["detailed"]["line_discount"]) == sum(
            D(i["discount"]) for sale in (a, b) for i in sale["items"]
        )
        assert D(s["detailed"]["gross_sales"]) == sum(D(i["gross"]) for sale in (a, b) for i in sale["items"])

    def test_customers_and_credit_do_not_change_the_figures(self, client_a, shop):
        customer = make_customer(client_a)
        sold(
            client_a,
            [item(shop["rice"], "2")],
            header={"sale_date": "2026-08-01", "customer_id": customer["id"]},
            amount_paid="0",
        )
        assert (
            summary(client_a, date_from="2026-08-01", date_to="2026-08-31")["detailed"]["net_sales"]
            == "100.00"
        )
        assert date(2026, 8, 1).isoformat() == "2026-08-01"


class TestExports:
    def rows(self, response):
        from tests.test_exports import read_csv

        table = read_csv(response.content)
        return [dict(zip(table[0], r, strict=True)) for r in table[1:]]

    def test_sales_summary_export(self, client_a, busy):
        response = client_a.get(
            "/api/v1/exports/sales-summary", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
        )
        assert response.status_code == 200 and response.content.startswith("﻿".encode())
        by_day = {r["Date"]: r for r in self.rows(response)}
        ten = by_day["2026-02-10"]
        assert set(by_day) == {"2026-02-10", "2026-02-11", "2026-02-12"}
        assert (ten["Detailed Net"], ten["Quick Net"], ten["Combined Net"]) == ("119.20", "900.00", "1019.20")
        assert (ten["Combined Gross"], ten["Combined Discount"]) == ("1148.00", "128.80")
        from decimal import Decimal as D

        assert D(ten["Combined Gross"]) - D(ten["Combined Discount"]) == D(ten["Combined Net"])

    def test_discount_report_export_and_plan(self, client_a, tenant_a, give_plan, busy):
        response = client_a.get(
            "/api/v1/exports/discount-report", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
        )
        data = self.rows(response)
        assert {(r["Offer"], r["Coupon Code"], r["Times Used"], r["Discount Given"]) for r in data} == {
            ("Festival", "", "1", "13.80"),
            ("Coupon", "FEST", "1", "13.80"),
        }
        give_plan(tenant_a, "free")
        assert client_a.get("/api/v1/exports/discount-report").status_code == 403
        assert client_a.get("/api/v1/exports/sales-summary").status_code == 200

    def test_owner_only_and_xlsx_dates(self, client_a, make_client, tenant_a, busy):
        from tests.test_exports import read_xlsx

        sheet = read_xlsx(
            client_a.get(
                "/api/v1/exports/sales-summary",
                params={"format": "xlsx", "date_from": "2026-02-01", "date_to": "2026-02-28"},
            ).content
        )
        first = dict(zip([c.value for c in sheet[1]], sheet[2], strict=True))
        assert (
            first["Date"].number_format == "dd/mm/yyyy" and first["Combined Net"].number_format == "#,##0.00"
        )
        assert (
            make_client(tenant_a, role=UserRole.STAFF).get("/api/v1/exports/sales-summary").status_code == 403
        )
