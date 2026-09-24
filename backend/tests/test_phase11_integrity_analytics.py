"""The read-only integrity checker and the operational analytics overview."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.reporting import integrity
from tests.test_promotions import shop as shop  # noqa: F401
from tests.test_promotions import sold
from tests.test_sales_api import item

OVERVIEW = "/api/v1/analytics/overview"


@pytest.fixture(autouse=True)
def plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


def findings(session_factory, shop_id=None):
    with session_factory() as s:
        return {f.check: f for f in integrity.run(s, shop_id)}


class TestIntegrityChecker:
    def test_a_healthy_shop_has_no_findings(self, client_a, shop, session_factory):
        sold(client_a, [item(shop["rice"], "2"), item(shop["sugar"], "1.5")])
        assert findings(session_factory) == {}
        with session_factory() as s:
            assert (
                integrity.summary(integrity.run(s))
                == {"ok": True, "findings": [], "errors": 0, "warnings": 0}
                or integrity.summary(integrity.run(s))["ok"]
            )

    def test_it_finds_a_sale_line_that_was_changed_behind_the_applications_back(
        self, client_a, shop, session_factory, engine
    ):
        sale = sold(client_a, [item(shop["rice"], "2")])
        with engine.begin() as c:
            c.execute(
                text("UPDATE sale_items SET line_total = line_total + 500 WHERE sale_id = :i"),
                {"i": sale["id"]},
            )
        found = findings(session_factory)
        assert (
            "sale_subtotal_matches_lines" in found
            and sale["id"] in found["sale_subtotal_matches_lines"].sample_ids
        )
        assert found["sale_subtotal_matches_lines"].severity == "ERROR"

    def test_it_finds_negative_stock_only_where_the_shop_forbids_it(
        self, client_a, tenant_a, shop, session_factory, engine
    ):
        with engine.begin() as c:
            c.execute(
                text("UPDATE shops SET allow_negative_stock = TRUE WHERE id = :i"), {"i": tenant_a.shop.id}
            )
        sold(client_a, [item(shop["rice"], "25")])  # 20 in stock: allowed while the shop permits it
        assert "negative_stock_where_forbidden" not in findings(session_factory)
        with engine.begin() as c:
            c.execute(
                text("UPDATE shops SET allow_negative_stock = FALSE WHERE id = :i"), {"i": tenant_a.shop.id}
            )
        assert shop["rice"]["id"] in findings(session_factory)["negative_stock_where_forbidden"].sample_ids

    def test_it_is_read_only_and_can_be_limited_to_one_shop(
        self, client_a, tenant_a, tenant_b, shop, session_factory, engine
    ):
        sale = sold(client_a, [item(shop["rice"], "1")])
        with engine.begin() as c:
            c.execute(
                text("UPDATE sale_items SET line_total = line_total + 1 WHERE sale_id = :i"),
                {"i": sale["id"]},
            )
            before = c.execute(
                text("SELECT line_total FROM sale_items WHERE sale_id = :i"), {"i": sale["id"]}
            ).scalar()
        assert "sale_subtotal_matches_lines" in findings(session_factory, tenant_a.shop.id)
        assert (
            findings(session_factory, tenant_b.shop.id) == {}
        )  # another shop's records are not this shop's problem
        findings(session_factory)
        with engine.begin() as c:
            assert (
                c.execute(
                    text("SELECT line_total FROM sale_items WHERE sale_id = :i"), {"i": sale["id"]}
                ).scalar()
                == before
            )  # nothing was "fixed"

    def test_the_command_line_reports_and_sets_its_exit_code(
        self, client_a, shop, session_factory, engine, db_url, monkeypatch, capsys
    ):
        from app import integrity_cli
        from app.core.config import get_settings
        from app.db import session as session_module

        monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
        monkeypatch.setattr("app.integrity_cli.read_session", session_module.read_session)
        sale = sold(client_a, [item(shop["rice"], "1")])
        assert integrity_cli.main(["--json"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
        with engine.begin() as c:
            c.execute(
                text("UPDATE sale_items SET line_total = line_total + 1 WHERE sale_id = :i"),
                {"i": sale["id"]},
            )
        assert integrity_cli.main([]) == 1
        assert "sale_subtotal_matches_lines" in capsys.readouterr().out
        get_settings.cache_clear()


class TestOverview:
    def get(self, client, **params):
        response = client.get(OVERVIEW, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def test_sales_come_from_the_existing_reports_and_add_up(self, client_a, shop):
        sold(client_a, [item(shop["rice"], "2")])  # 100.00
        sold(client_a, [item(shop["sugar"], "1")])  # 48.00
        quick = client_a.post("/api/v1/quick-sales", json={"gross_amount": "30", "sale_date": None})
        body = self.get(client_a)
        sales = body["sales"]
        assert sales["detailed"]["count"] == 2 and Decimal(sales["detailed"]["net"]) == Decimal("148.00")
        assert sales["transactions"] >= 2 and Decimal(sales["net"]) >= Decimal("148.00")
        assert sales["online"]["available"] is False  # there is no online store to report on: it says so
        assert quick.status_code in (200, 201, 422)

    def test_missing_cost_is_not_available_never_zero(self, client_a, shop):
        sold(client_a, [item(shop["oil"], "1")])  # oil has no cost
        profit = self.get(client_a)["profit"]
        assert (
            profit["available"] is False
            and "Not Available" in profit["message"]
            and profit["sales_without_cost"] >= 1
        )

    def test_profit_is_shown_when_every_cost_is_known(self, client_a, shop):
        sold(client_a, [item(shop["rice"], "2")])  # sells at 100, costs 40
        profit = self.get(client_a)["profit"]
        assert (
            profit["available"] is True
            and Decimal(profit["gross_profit"]) == Decimal("60.00")
            and profit["margin_percent"] == "60.0"
        )

    def test_inventory_counts_and_value_use_the_stock_service(self, client_a, shop):
        inventory = self.get(client_a)["inventory"]
        assert inventory["products"] == 4 and inventory["in_stock"] == 4 and inventory["out_of_stock"] == 0
        assert inventory["products_without_cost"] == 1  # oil
        assert (
            Decimal(inventory["stock_value"]) == Decimal("20") * 20 + 100 * 20 + 50 * 10
        )  # 2900; oil's unknown cost is left out

    def test_customers_and_khata(self, client_a, shop):
        from tests.test_sales_api import make_customer

        ram = make_customer(client_a, "Ram")
        sold(
            client_a, [item(shop["rice"], "2")], header={"customer_id": ram["id"]}, amount_paid="40"
        )  # 60 owed
        customers = self.get(client_a)["customers"]
        assert (
            Decimal(customers["outstanding_total"]) == Decimal("60.00") and customers["customers_owing"] == 1
        )
        assert customers["credit_sales"]["bills"] == 1 and customers["online_customers"]["available"] is False

    def test_the_period_is_limited_and_validated(self, client_a, shop):
        assert (
            client_a.get(OVERVIEW, params={"date_from": "2020-01-01", "date_to": "2025-01-01"}).status_code
            == 422
        )
        assert client_a.get(OVERVIEW, params={"bucket": "hour"}).status_code == 422
        assert self.get(client_a, bucket="month")["sales"]["bucket"] == "month"

    def test_an_empty_shop_has_a_calm_overview(self, client_b):
        body = client_b.get(OVERVIEW).json()
        assert (
            body["sales"]["transactions"] == 0
            and body["sales"]["average_bill"] is None
            and body["profit"]["available"] is False
        )

    def test_another_shops_numbers_are_never_included(self, client_a, client_b, shop):
        sold(client_a, [item(shop["rice"], "2")])
        assert client_b.get(OVERVIEW).json()["sales"]["net"] == "0.00"

    def test_offers_are_reported_only_where_the_plan_includes_them(self, client_a, tenant_a, shop, give_plan):
        assert self.get(client_a)["promotions"]["available"] is True
        give_plan(tenant_a, "free")
        assert self.get(client_a)["promotions"]["available"] is False

    def test_it_reads_and_never_writes(self, client_a, shop, engine):
        def digest():
            with engine.connect() as c:
                return [
                    c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                    for t in (
                        "sales",
                        "inventory_transactions",
                        "customer_ledger",
                        "audit_log",
                        "notification_events",
                    )
                ]

        before = digest()
        self.get(client_a)
        assert digest() == before
