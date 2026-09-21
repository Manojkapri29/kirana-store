"""Promotion and coupon-usage exports: columns, formats, formula safety, scope, owner-only."""

from datetime import date

import pytest

from app.models.enums import UserRole
from tests.test_exports import INJECTIONS, read_csv, read_xlsx
from tests.test_promotions import SALES, create, live, sold, state
from tests.test_promotions import shop as shop  # noqa: F401  (the shared fixture)
from tests.test_sales_api import item, make_customer

EXPORTS = "/api/v1/exports"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


def rows(response):
    table = read_csv(response.content)
    return [dict(zip(table[0], row, strict=True)) for row in table[1:]]


class TestPromotionsExport:
    def test_one_row_per_offer_with_its_terms_status_and_use(self, client_a, shop):
        live(client_a, name="Festival", percent="10", coupon_code="SAVE10", usage_limit=5, max_discount="30")
        paused = live(
            client_a, name="Weekend", promo_type="AMOUNT", percent=None, amount="20", stackable=True
        )
        state(client_a, paused, "pause")
        create(client_a, name="Draft one")
        sold(client_a, [item(shop["rice"], "4")], header={"coupon_code": "SAVE10"})

        response = client_a.get(f"{EXPORTS}/promotions")

        assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith('attachment; filename="promotions_')
        assert response.headers["cache-control"] == "no-store" and response.content.startswith("﻿".encode())
        by_name = {r["Offer"]: r for r in rows(response)}
        assert set(by_name) == {"Festival", "Weekend", "Draft one"}
        festival = by_name["Festival"]
        assert (festival["What It Gives"], festival["Status"], festival["Coupon Code"]) == (
            "10% off (up to 30.00)",
            "Active",
            "SAVE10",
        )
        assert (
            festival["Times Used"],
            festival["Discount Given"],
            festival["Usage Limit"],
            festival["Can Combine"],
        ) == ("1", "20.00", "5", "No")
        assert (by_name["Weekend"]["Status"], by_name["Weekend"]["Can Combine"]) == ("Paused", "Yes")
        assert by_name["Draft one"]["Status"] == "Draft"

    def test_filters(self, client_a):
        live(client_a, name="A", coupon_code="AAA")
        create(client_a, name="B")
        assert len(rows(client_a.get(f"{EXPORTS}/promotions", params={"coupon_only": "true"}))) == 1
        assert len(rows(client_a.get(f"{EXPORTS}/promotions", params={"status": "DRAFT"}))) == 1
        assert len(rows(client_a.get(f"{EXPORTS}/promotions", params={"q": "b"}))) == 1

    def test_xlsx_has_real_numbers_and_dates(self, client_a):
        live(
            client_a,
            name="Timed",
            percent="10",
            max_discount="50",
            starts_at="2026-01-10T00:00:00+05:30",
            ends_at="2099-01-10T00:00:00+05:30",
        )
        sheet = read_xlsx(client_a.get(f"{EXPORTS}/promotions", params={"format": "xlsx"}).content)
        header = [c.value for c in sheet[1]]
        row = dict(zip(header, sheet[2], strict=True))
        assert (
            row["Starts"].value.date() == date(2026, 1, 10)
            and row["Starts"].number_format == "dd/mm/yyyy hh:mm"
        )
        assert (
            float(row["Maximum Discount"].value) == 50.0
            and row["Maximum Discount"].number_format == "#,##0.00"
        )

    @pytest.mark.parametrize("fmt", ["csv", "xlsx"])
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_formula_like_text_is_neutralised(self, client_a, payload, fmt):
        create(client_a, name=payload[:120], description=payload)
        content = client_a.get(f"{EXPORTS}/promotions", params={"format": fmt}).content
        if fmt == "csv":
            cells = [c for row in read_csv(content) for c in row]
        else:
            cells = [
                str(c.value) for row in read_xlsx(content).iter_rows() for c in row if c.value is not None
            ]
        assert [c for c in cells if c.startswith(("=", "+", "-", "@", "\t", "\r"))] == []

    def test_scope_and_owner_only(self, client_a, client_b, make_client, tenant_a):
        live(client_a, name="Mine")
        live(client_b, name="Theirs")
        assert [r["Offer"] for r in rows(client_a.get(f"{EXPORTS}/promotions"))] == ["Mine"]
        assert make_client(tenant_a, role=UserRole.STAFF).get(f"{EXPORTS}/promotions").status_code == 403


class TestUsageExport:
    @pytest.fixture
    def used(self, client_a, shop):
        asha = make_customer(client_a, "Asha Devi")
        live(client_a, name="Festival", percent="10", coupon_code="SAVE10")
        a = sold(
            client_a,
            [item(shop["rice"], "2")],
            header={"coupon_code": "SAVE10", "customer_id": asha["id"], "sale_date": "2026-02-10"},
        )
        b = sold(
            client_a, [item(shop["rice"], "4")], header={"coupon_code": "SAVE10", "sale_date": "2026-03-10"}
        )
        client_a.post(f"{SALES}/{b['id']}/void", json={"reason": "wrong"})
        return a, b

    def test_rows_come_from_the_frozen_snapshots(self, client_a, used):
        a, b = used
        data = rows(client_a.get(f"{EXPORTS}/promotion-usage"))
        by_no = {r["Invoice No"]: r for r in data}
        assert set(by_no) == {a["invoice_no"], b["invoice_no"]}
        first = by_no[a["invoice_no"]]
        assert (
            first["Offer"],
            first["What It Gave"],
            first["Coupon Code"],
            first["Discount"],
            first["Customer"],
        ) == ("Festival", "10% off", "SAVE10", "10.00", "Asha Devi")
        assert first["Sale Status"] == "Posted" and by_no[b["invoice_no"]]["Sale Status"] == "Void"
        assert "the whole bill" in first["Why It Applied"]

    def test_the_coupon_usage_report_and_filters(self, client_a, shop, used):
        live(client_a, name="Auto", percent="5")
        sold(client_a, [item(shop["rice"])])
        assert len(rows(client_a.get(f"{EXPORTS}/promotion-usage"))) == 3
        assert {
            r["Coupon Code"]
            for r in rows(client_a.get(f"{EXPORTS}/promotion-usage", params={"coupon_only": "true"}))
        } == {"SAVE10"}
        assert (
            len(
                rows(
                    client_a.get(
                        f"{EXPORTS}/promotion-usage",
                        params={"date_from": "2026-03-01", "date_to": "2026-03-31"},
                    )
                )
            )
            == 1
        )
        assert (
            client_a.get(
                f"{EXPORTS}/promotion-usage", params={"date_from": "2026-04-01", "date_to": "2026-03-01"}
            ).status_code
            == 422
        )

    def test_xlsx_dates_are_real_dates(self, client_a, used):
        sheet = read_xlsx(client_a.get(f"{EXPORTS}/promotion-usage", params={"format": "xlsx"}).content)
        header = [c.value for c in sheet[1]]
        first = dict(zip(header, sheet[2], strict=True))
        assert first["Date"].number_format == "dd/mm/yyyy" and first["Date"].value.date() in (
            date(2026, 2, 10),
            date(2026, 3, 10),
        )
        assert first["Discount"].number_format == "#,##0.00"

    def test_scope_and_owner_only(self, client_a, client_b, make_client, tenant_a, used):
        assert rows(client_b.get(f"{EXPORTS}/promotion-usage")) == []
        assert make_client(tenant_a, role=UserRole.STAFF).get(f"{EXPORTS}/promotion-usage").status_code == 403
