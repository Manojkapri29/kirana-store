"""Inventory API: opening stock, current stock, the inventory list, and transaction history."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import InventoryTransaction
from tests.factories import today_in_shop_timezone

PRODUCTS = "/api/v1/products"
INV = "/api/v1/inventory"


def make_product(client, tenant, units, **overrides) -> dict:
    data = {
        "sku": "RICE",
        "name": "Rice 5kg",
        "category_id": tenant.category.id,
        "unit_id": units["pcs"],
        "selling_price": "250",
    }
    data.update(overrides)
    response = client.post(PRODUCTS, json=data)
    assert response.status_code == 201, response.text
    return response.json()["product"]


def opening(client, product_id, quantity="20", **extra):
    return client.post(f"{INV}/opening-stock", json={"product_id": product_id, "quantity": quantity, **extra})


def message(response) -> str:
    return response.json()["detail"][0]["msg"]


class TestOpeningStockEndpoint:
    def test_records_opening_stock_and_returns_the_new_balance(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)

        response = opening(client_a, product["id"], "20", unit_cost="42.50", note="Counted at start")

        assert response.status_code == 201
        data = response.json()
        assert data["transaction"]["txn_type"] == "OPENING"
        assert data["transaction"]["qty_delta"] == "20.000"
        assert data["transaction"]["balance_after"] == "20.000"
        assert data["transaction"]["unit_cost"] == "42.50"
        assert data["transaction"]["reference_type"] == "PRODUCT"
        assert data["transaction"]["created_by_name"] == "Test Owner"
        assert (data["stock"]["current_stock"], data["stock"]["status"]) == ("20.000", "IN_STOCK")

    def test_the_ledger_row_is_really_written(self, client_a, tenant_a, units, fresh):
        product = make_product(client_a, tenant_a, units)
        opening(client_a, product["id"], "20")

        rows = fresh(lambda s: s.scalars(select(InventoryTransaction)).all())

        assert [(r.txn_type.value, str(r.qty_delta), r.shop_id) for r in rows] == [
            ("OPENING", "20.000", tenant_a.shop.id)
        ]

    def test_an_unknown_cost_is_null_in_the_response(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)

        assert opening(client_a, product["id"], "5").json()["transaction"]["unit_cost"] is None

    def test_fractional_quantity_for_a_weight_unit(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units, unit_id=units["kg"])

        assert opening(client_a, product["id"], "2.500").json()["stock"]["current_stock"] == "2.500"

    def test_a_past_date_is_kept(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)
        past = (today_in_shop_timezone() - timedelta(days=30)).isoformat()

        assert opening(client_a, product["id"], "5", txn_date=past).json()["transaction"]["txn_date"] == past

    @pytest.mark.parametrize(
        ("quantity", "fragment"),
        [
            ("0", "greater than zero"),
            ("-5", "cannot be negative"),
            ("1.5", "whole number"),
            ("abc", "valid number"),
        ],
    )
    def test_invalid_quantities(self, client_a, tenant_a, units, quantity, fragment):
        product = make_product(client_a, tenant_a, units)

        response = opening(client_a, product["id"], quantity)

        assert response.status_code == 422 and fragment in message(response).lower()

    def test_a_json_float_quantity_is_refused(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units, unit_id=units["kg"])

        response = client_a.post(f"{INV}/opening-stock", json={"product_id": product["id"], "quantity": 2.5})

        assert response.status_code == 422 and "as text" in message(response)

    def test_the_date_cannot_be_in_the_future(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)
        future = (today_in_shop_timezone() + timedelta(days=3)).isoformat()

        assert opening(client_a, product["id"], "5", txn_date=future).status_code == 422

    def test_a_second_opening_is_refused_and_the_stock_is_unchanged(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)
        opening(client_a, product["id"], "20")

        response = opening(client_a, product["id"], "5")

        assert response.status_code == 409 and "already been recorded" in message(response)
        assert client_a.get(f"{INV}/products/{product['id']}").json()["current_stock"] == "20.000"

    def test_unknown_fields_are_refused(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)

        response = client_a.post(
            f"{INV}/opening-stock",
            json={"product_id": product["id"], "quantity": "5", "current_stock": "999"},
        )

        assert response.status_code == 422

    def test_unknown_product_is_404(self, client_a):
        assert opening(client_a, 9999).status_code == 404


class TestStockAndListing:
    def test_current_stock_of_one_product(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units, reorder_level="5")
        opening(client_a, product["id"], "3")

        stock = client_a.get(f"{INV}/products/{product['id']}").json()

        assert stock["current_stock"] == "3.000" and stock["status"] == "LOW_STOCK"
        assert (stock["sku"], stock["unit_code"], stock["reorder_level"]) == ("RICE", "pcs", "5.000")

    def test_a_product_without_movements_has_zero_stock(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)

        stock = client_a.get(f"{INV}/products/{product['id']}").json()

        assert (stock["current_stock"], stock["status"]) == ("0.000", "OUT_OF_STOCK")

    @pytest.fixture
    def stocked(self, client_a, tenant_a, units):
        for sku, name, stock in (("OK", "Atta", "50"), ("LOW", "Besan", "3"), ("OUT", "Chana", None)):
            product = make_product(client_a, tenant_a, units, sku=sku, name=name, reorder_level="5")
            if stock:
                opening(client_a, product["id"], stock)

    def test_inventory_for_all_products(self, client_a, stocked):
        data = client_a.get(INV).json()

        assert data["total"] == 3
        assert [(i["sku"], i["current_stock"], i["status"]) for i in data["items"]] == [
            ("OK", "50.000", "IN_STOCK"),
            ("LOW", "3.000", "LOW_STOCK"),
            ("OUT", "0.000", "OUT_OF_STOCK"),
        ]
        first = data["items"][0]
        assert {"product_id", "sku", "name", "unit_code", "reorder_level", "status"} <= set(first)

    @pytest.mark.parametrize(
        ("stock_status", "skus"), [("IN_STOCK", ["OK"]), ("LOW_STOCK", ["LOW"]), ("OUT_OF_STOCK", ["OUT"])]
    )
    def test_filter_by_stock_status(self, client_a, stocked, stock_status, skus):
        items = client_a.get(INV, params={"stock_status": stock_status}).json()["items"]

        assert [i["sku"] for i in items] == skus

    def test_search_paging_and_bad_status(self, client_a, stocked):
        assert [i["sku"] for i in client_a.get(INV, params={"q": "besan"}).json()["items"]] == ["LOW"]
        page = client_a.get(INV, params={"limit": 1, "offset": 2}).json()
        assert (page["total"], [i["sku"] for i in page["items"]]) == (3, ["OUT"])
        assert client_a.get(INV, params={"stock_status": "NOPE"}).status_code == 422

    def test_deactivated_products_leave_the_default_inventory_but_keep_their_stock(
        self, client_a, tenant_a, units
    ):
        product = make_product(client_a, tenant_a, units)
        opening(client_a, product["id"], "8")
        client_a.post(f"{PRODUCTS}/{product['id']}/deactivate")

        assert client_a.get(INV).json()["total"] == 0
        inactive = client_a.get(INV, params={"status": "inactive"}).json()["items"]
        assert [(i["sku"], i["current_stock"]) for i in inactive] == [("RICE", "8.000")]


class TestHistory:
    def test_product_history_shows_type_quantity_balance_and_who(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)
        opening(client_a, product["id"], "20", note="Start")

        data = client_a.get(f"{INV}/products/{product['id']}/transactions").json()

        assert data["total"] == 1
        row = data["items"][0]
        assert (row["txn_type"], row["qty_delta"], row["balance_after"], row["note"]) == (
            "OPENING", "20.000", "20.000", "Start",
        )  # fmt: skip
        assert (row["sku"], row["product_name"], row["created_by_name"]) == ("RICE", "Rice 5kg", "Test Owner")

    def test_all_transactions_with_filters(self, client_a, tenant_a, units):
        first = make_product(client_a, tenant_a, units, sku="A")
        second = make_product(client_a, tenant_a, units, sku="B")
        opening(client_a, first["id"], "10")
        opening(client_a, second["id"], "4")

        everything = client_a.get(f"{INV}/transactions").json()
        only_second = client_a.get(f"{INV}/transactions", params={"product_id": second["id"]}).json()
        only_opening = client_a.get(f"{INV}/transactions", params={"txn_type": "OPENING"}).json()
        none_after_tomorrow = client_a.get(
            f"{INV}/transactions",
            params={"date_from": (today_in_shop_timezone() + timedelta(days=2)).isoformat()},
        ).json()

        assert everything["total"] == 2
        assert [r["sku"] for r in only_second["items"]] == ["B"]
        assert only_opening["total"] == 2 and none_after_tomorrow["total"] == 0

    def test_history_of_an_unknown_product_is_404(self, client_a):
        assert client_a.get(f"{INV}/products/9999/transactions").status_code == 404


class TestShopIsolation:
    def test_one_shop_cannot_touch_or_see_another_shops_inventory(
        self, client_a, client_b, tenant_a, tenant_b, units
    ):
        mine = make_product(client_a, tenant_a, units, sku="MINE")
        theirs = make_product(client_b, tenant_b, units, sku="THEIRS")
        opening(client_a, mine["id"], "20")
        opening(client_b, theirs["id"], "99")

        # B cannot read, write or list A's inventory.
        assert client_b.get(f"{INV}/products/{mine['id']}").status_code == 404
        assert client_b.get(f"{INV}/products/{mine['id']}/transactions").status_code == 404
        assert opening(client_b, mine["id"], "1").status_code == 404
        assert client_b.get(f"{INV}/transactions", params={"product_id": mine["id"]}).json()["total"] == 0
        assert [i["sku"] for i in client_b.get(INV).json()["items"]] == ["THEIRS"]
        assert client_b.get(INV, params={"q": "mine"}).json()["total"] == 0
        # ... and A's stock is untouched.
        assert client_a.get(f"{INV}/products/{mine['id']}").json()["current_stock"] == "20.000"

    def test_each_shop_sees_only_its_own_history_rows(
        self, client_a, client_b, tenant_a, tenant_b, units, fresh
    ):
        opening(client_a, make_product(client_a, tenant_a, units)["id"], "20")
        opening(client_b, make_product(client_b, tenant_b, units)["id"], "7")

        assert [r["qty_delta"] for r in client_a.get(f"{INV}/transactions").json()["items"]] == ["20.000"]
        assert [r["qty_delta"] for r in client_b.get(f"{INV}/transactions").json()["items"]] == ["7.000"]
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == 2
