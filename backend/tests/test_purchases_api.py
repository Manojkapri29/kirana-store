"""Purchases API: the draft/post/void workflow, validation, stock and cost effects, isolation, filters."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, InventoryTransaction, Purchase, PurchaseItem
from tests.factories import today_in_shop_timezone
from tests.test_business_types import REQUESTED_TYPES

API = "/api/v1/purchases"
PRODUCTS = "/api/v1/products"
INV = "/api/v1/inventory"
SUPPLIERS = "/api/v1/suppliers"


def make_supplier(client, name="Sharma Traders"):
    response = client.post(SUPPLIERS, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["supplier"]


def make_product(client, tenant, units, sku="RICE", unit="pcs", **overrides):
    body = {
        "sku": sku,
        "name": f"Product {sku}",
        "category_id": tenant.category.id,
        "unit_id": units[unit],
        "selling_price": "50",
        **overrides,
    }
    response = client.post(PRODUCTS, json=body)
    assert response.status_code == 201, response.text
    return response.json()["product"]


def line(product, quantity="10", unit_cost="20", **extra):
    return {"product_id": product["id"], "quantity": quantity, "unit_cost": unit_cost, **extra}


def draft(client, supplier, items=None, **header):
    response = client.post(API, json={"supplier_id": supplier["id"], "items": items or [], **header})
    assert response.status_code == 201, response.text
    return response.json()


def post(client, purchase):
    return client.post(f"{API}/{purchase['id']}/post")


def posted(client, supplier, items, **header):
    response = post(client, draft(client, supplier, items, **header))
    assert response.status_code == 200, response.text
    return response.json()


def stock(client, product):
    return Decimal(client.get(f"{INV}/products/{product['id']}").json()["current_stock"])


def avg_cost(client, product):
    value = client.get(f"{PRODUCTS}/{product['id']}").json()["avg_cost"]
    return None if value is None else Decimal(value)


def errors(response) -> dict[str, str]:
    return {".".join(str(p) for p in item["loc"][1:]): item["msg"] for item in response.json()["detail"]}


@pytest.fixture
def setup(client_a, tenant_a, units):
    supplier = make_supplier(client_a)
    rice = make_product(client_a, tenant_a, units, "RICE")
    dal = make_product(client_a, tenant_a, units, "DAL")
    return supplier, rice, dal


class TestCreateDraft:
    def test_a_draft_has_no_number_and_does_not_touch_stock(self, client_a, setup):
        supplier, rice, _ = setup

        purchase = draft(client_a, supplier, [line(rice, "100", "20")], supplier_invoice_no="INV-1")

        assert purchase["status"] == "DRAFT" and purchase["purchase_no"] is None
        assert purchase["total_amount"] == "2000.00" and purchase["item_count"] == 1
        assert purchase["supplier_name"] == "Sharma Traders"
        assert purchase["purchase_date"] == today_in_shop_timezone().isoformat()
        assert purchase["created_by_name"] == "Test Owner"
        assert stock(client_a, rice) == 0 and avg_cost(client_a, rice) is None

    def test_an_empty_draft_can_be_started_and_filled_later(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier)

        filled = client_a.post(f"{API}/{purchase['id']}/items", json=line(rice, "5", "10"))

        assert filled.status_code == 201 and filled.json()["total_amount"] == "50.00"

    def test_line_total_is_quantity_times_price_minus_discount(self, client_a, setup):
        supplier, rice, _ = setup

        purchase = draft(client_a, supplier, [line(rice, "3", "33.33", discount="0.99")])

        item = purchase["items"][0]
        assert item["line_total"] == "99.00"  # 3 x 33.33 = 99.99, minus 0.99
        assert item["unit_cost"] == "33.33" and item["discount"] == "0.99"
        assert item["unit_code"] == "pcs"

    def test_the_total_is_the_sum_of_the_lines(self, client_a, tenant_a, units, setup):
        supplier, rice, _ = setup
        kg = make_product(client_a, tenant_a, units, "SUGAR", unit="kg")

        purchase = draft(client_a, supplier, [line(rice, "10", "20"), line(kg, "2.5", "0.10")])

        assert purchase["total_amount"] == "200.25"

    def test_a_line_is_rounded_to_whole_paise_half_up(self, client_a, tenant_a, units, setup):
        supplier, *_ = setup
        kg = make_product(client_a, tenant_a, units, "SUGAR", unit="kg")

        purchase = draft(client_a, supplier, [line(kg, "0.005", "10.00")])  # 0.05 exactly

        assert purchase["items"][0]["line_total"] == "0.05"
        purchase = draft(
            client_a, supplier, [line(kg, "0.5", "0.01")], supplier_invoice_no="X"
        )  # 0.005 -> 0.01
        assert purchase["items"][0]["line_total"] == "0.01"

    def test_editing_a_draft_changes_lines_and_total(self, client_a, setup):
        supplier, rice, dal = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20")])
        item_id = purchase["items"][0]["id"]

        edited = client_a.patch(f"{API}/{purchase['id']}/items/{item_id}", json={"quantity": "12"}).json()
        replaced = client_a.put(f"{API}/{purchase['id']}/items", json={"items": [line(dal, "1", "5")]}).json()

        assert edited["items"][0]["quantity"] == "12.000" and edited["total_amount"] == "240.00"
        assert [i["sku"] for i in replaced["items"]] == ["DAL"] and replaced["total_amount"] == "5.00"

    def test_header_of_a_draft_can_be_edited(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)])

        response = client_a.patch(
            f"{API}/{purchase['id']}", json={"supplier_invoice_no": "  B-77 ", "notes": "Urgent"}
        )

        assert response.status_code == 200
        assert response.json()["supplier_invoice_no"] == "B-77" and response.json()["notes"] == "Urgent"

    def test_there_is_no_delete_endpoint(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)])

        assert client_a.delete(f"{API}/{purchase['id']}").status_code == 405


class TestValidation:
    def test_supplier_is_required_and_must_exist(self, client_a, setup):
        assert client_a.post(API, json={"items": []}).status_code == 422
        response = client_a.post(API, json={"supplier_id": 99999, "items": []})
        assert response.status_code == 422 and "supplier_id" in errors(response)

    def test_an_inactive_supplier_cannot_be_chosen(self, client_a, setup):
        supplier, rice, _ = setup
        client_a.post(f"{SUPPLIERS}/{supplier['id']}/deactivate")

        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice)]})

        assert response.status_code == 422 and "inactive" in errors(response)["supplier_id"]

    def test_the_date_cannot_be_in_the_future(self, client_a, setup):
        supplier, *_ = setup
        tomorrow = (today_in_shop_timezone() + timedelta(days=1)).isoformat()

        response = client_a.post(API, json={"supplier_id": supplier["id"], "purchase_date": tomorrow})

        assert response.status_code == 422 and "purchase_date" in errors(response)

    def test_an_invalid_date_is_refused(self, client_a, setup):
        supplier, *_ = setup
        assert (
            client_a.post(
                API, json={"supplier_id": supplier["id"], "purchase_date": "31/02/2026"}
            ).status_code
            == 422
        )

    def test_a_past_date_is_accepted(self, client_a, setup):
        supplier, *_ = setup
        purchase = draft(client_a, supplier, purchase_date="2026-01-15")
        assert purchase["purchase_date"] == "2026-01-15"

    @pytest.mark.parametrize("quantity", ["0", "0.000"])
    def test_quantity_must_be_more_than_zero(self, client_a, setup, quantity):
        supplier, rice, _ = setup

        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice, quantity)]})

        assert response.status_code == 422 and "items.0.quantity" in errors(response)

    @pytest.mark.parametrize("quantity", ["-1", "abc", "1.2345", ""])
    def test_a_bad_quantity_is_refused(self, client_a, setup, quantity):
        supplier, rice, _ = setup
        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice, quantity)]})
        assert response.status_code == 422

    @pytest.mark.parametrize("price", ["-5", "x", "1.005", ""])
    def test_a_bad_price_is_refused(self, client_a, setup, price):
        supplier, rice, _ = setup
        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice, "1", price)]})
        assert response.status_code == 422

    def test_a_price_of_zero_is_allowed_for_free_goods(self, client_a, setup):
        supplier, rice, _ = setup
        assert draft(client_a, supplier, [line(rice, "5", "0")])["total_amount"] == "0.00"

    def test_amounts_must_be_sent_as_text_not_numbers(self, client_a, setup):
        supplier, rice, _ = setup
        item = {"product_id": rice["id"], "quantity": 1, "unit_cost": 12.5}
        assert client_a.post(API, json={"supplier_id": supplier["id"], "items": [item]}).status_code == 422

    def test_a_discount_larger_than_the_line_is_refused(self, client_a, setup):
        supplier, rice, _ = setup

        response = client_a.post(
            API, json={"supplier_id": supplier["id"], "items": [line(rice, "1", "10", discount="10.01")]}
        )

        assert response.status_code == 422 and "items.0.discount" in errors(response)

    def test_a_discount_equal_to_the_line_makes_it_free(self, client_a, setup):
        supplier, rice, _ = setup
        assert draft(client_a, supplier, [line(rice, "1", "10", discount="10")])["total_amount"] == "0.00"

    def test_fractions_are_refused_for_units_that_cannot_be_split(self, client_a, setup):
        supplier, rice, _ = setup  # pieces

        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice, "2.5")]})

        assert response.status_code == 422 and "cannot be split" in errors(response)["items.0.quantity"]

    def test_fractions_are_allowed_for_units_that_can_be_split(self, client_a, tenant_a, units, setup):
        supplier, *_ = setup
        kg = make_product(client_a, tenant_a, units, "SUGAR", unit="kg")
        assert draft(client_a, supplier, [line(kg, "2.5", "40")])["total_amount"] == "100.00"

    def test_an_unknown_product_is_refused(self, client_a, setup):
        supplier, *_ = setup
        response = client_a.post(
            API,
            json={
                "supplier_id": supplier["id"],
                "items": [{"product_id": 99999, "quantity": "1", "unit_cost": "1"}],
            },
        )
        assert response.status_code == 422 and "items.0.product_id" in errors(response)

    def test_an_inactive_product_is_refused(self, client_a, setup):
        supplier, rice, _ = setup
        client_a.post(f"{PRODUCTS}/{rice['id']}/deactivate")
        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice)]})
        assert response.status_code == 422 and "inactive" in errors(response)["items.0.product_id"]

    def test_a_unit_that_is_not_the_products_unit_is_refused(self, client_a, units, setup):
        supplier, rice, _ = setup
        response = client_a.post(
            API, json={"supplier_id": supplier["id"], "items": [line(rice, unit_id=units["kg"])]}
        )
        assert response.status_code == 422 and "items.0.unit_id" in errors(response)

    def test_every_bad_line_is_reported_at_once_with_its_position(self, client_a, setup):
        supplier, rice, dal = setup

        response = client_a.post(
            API,
            json={
                "supplier_id": supplier["id"],
                "items": [
                    line(rice),
                    line(dal, "2.5"),
                    {"product_id": 99999, "quantity": "1", "unit_cost": "1"},
                ],
            },
        )

        assert response.status_code == 422
        assert set(errors(response)) == {"items.1.quantity", "items.2.product_id"}

    def test_a_purchase_has_a_limit_on_lines(self, client_a, setup):
        supplier, rice, _ = setup
        response = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(rice)] * 201})
        assert response.status_code == 422

    def test_unknown_fields_are_refused(self, client_a, setup):
        supplier, *_ = setup
        assert (
            client_a.post(API, json={"supplier_id": supplier["id"], "total_amount": "1"}).status_code == 422
        )
        assert client_a.post(API, json={"supplier_id": supplier["id"], "status": "POSTED"}).status_code == 422

    def test_a_too_long_invoice_number_is_refused(self, client_a, setup):
        supplier, *_ = setup
        response = client_a.post(API, json={"supplier_id": supplier["id"], "supplier_invoice_no": "x" * 51})
        assert response.status_code == 422

    def test_posting_needs_at_least_one_item(self, client_a, setup):
        supplier, *_ = setup
        empty = draft(client_a, supplier)

        response = post(client_a, empty)

        assert response.status_code == 422 and "items" in errors(response)
        assert client_a.get(f"{API}/{empty['id']}").json()["status"] == "DRAFT"


class TestSupplierInvoiceNumbers:
    def test_the_same_invoice_cannot_be_entered_twice_for_one_supplier(self, client_a, setup):
        supplier, rice, _ = setup
        draft(client_a, supplier, [line(rice)], supplier_invoice_no="INV-9")

        response = client_a.post(
            API, json={"supplier_id": supplier["id"], "supplier_invoice_no": "inv-9", "items": [line(rice)]}
        )

        assert (
            response.status_code == 409 and "already been entered" in errors(response)["supplier_invoice_no"]
        )

    def test_the_same_invoice_number_is_fine_for_a_different_supplier(self, client_a, setup):
        supplier, rice, _ = setup
        other = make_supplier(client_a, "Gupta Stores")
        draft(client_a, supplier, [line(rice)], supplier_invoice_no="INV-9")

        assert (
            draft(client_a, other, [line(rice)], supplier_invoice_no="INV-9")["supplier_invoice_no"]
            == "INV-9"
        )

    def test_purchases_without_an_invoice_number_never_clash(self, client_a, setup):
        supplier, rice, _ = setup
        draft(client_a, supplier, [line(rice)])
        draft(client_a, supplier, [line(rice)])

    def test_changing_a_draft_to_a_used_invoice_number_is_refused(self, client_a, setup):
        supplier, rice, _ = setup
        draft(client_a, supplier, [line(rice)], supplier_invoice_no="A-1")
        other = draft(client_a, supplier, [line(rice)], supplier_invoice_no="A-2")

        response = client_a.patch(f"{API}/{other['id']}", json={"supplier_invoice_no": "A-1"})

        assert response.status_code == 409

    def test_resaving_a_draft_with_its_own_invoice_number_is_fine(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)], supplier_invoice_no="A-1")
        assert (
            client_a.patch(f"{API}/{purchase['id']}", json={"supplier_invoice_no": "A-1"}).status_code == 200
        )

    def test_a_voided_purchase_releases_its_invoice_number(self, client_a, setup):
        supplier, rice, _ = setup
        first = posted(client_a, supplier, [line(rice)], supplier_invoice_no="INV-1")
        client_a.post(f"{API}/{first['id']}/void", json={"reason": "Entered twice"})

        again = draft(client_a, supplier, [line(rice)], supplier_invoice_no="INV-1")

        assert again["supplier_invoice_no"] == "INV-1"


class TestPosting:
    def test_posting_numbers_the_purchase_and_adds_the_stock(self, client_a, setup):
        supplier, rice, dal = setup
        purchase = draft(client_a, supplier, [line(rice, "100", "20"), line(dal, "8", "60")])

        response = post(client_a, purchase)

        assert response.status_code == 200
        data = response.json()
        year = today_in_shop_timezone()
        start = year.year if year.month >= 4 else year.year - 1
        assert data["status"] == "POSTED"
        assert data["purchase_no"] == f"PUR/{start}-{(start + 1) % 100:02d}/0001"
        assert data["posted_at"] and data["posted_by_name"] == "Test Owner"
        assert stock(client_a, rice) == 100 and stock(client_a, dal) == 8
        assert avg_cost(client_a, rice) == Decimal("20.00") and avg_cost(client_a, dal) == Decimal("60.00")

    def test_the_brief_example_100_at_20_then_50_at_30_gives_150_units_at_23_33(self, client_a, setup):
        supplier, rice, _ = setup

        first = posted(client_a, supplier, [line(rice, "100", "20")])
        second = posted(client_a, supplier, [line(rice, "50", "30")])

        assert stock(client_a, rice) == 150 and avg_cost(client_a, rice) == Decimal("23.33")
        # the posted lines remember what happened at posting
        item = second["items"][0]
        assert (item["stock_before"], item["stock_after"]) == ("100.000", "150.000")
        assert (item["avg_cost_before"], item["avg_cost_after"]) == ("20.00", "23.33")
        assert first["items"][0]["avg_cost_before"] is None and first["items"][0]["avg_cost_after"] == "20.00"

    def test_purchase_numbers_run_in_sequence(self, client_a, setup):
        supplier, rice, _ = setup
        numbers = [posted(client_a, supplier, [line(rice)])["purchase_no"] for _ in range(3)]
        assert [n.rsplit("/", 1)[1] for n in numbers] == ["0001", "0002", "0003"]

    def test_only_posting_consumes_a_number(self, client_a, setup):
        supplier, rice, _ = setup
        draft(client_a, supplier, [line(rice)])
        draft(client_a, supplier, [line(rice)])
        assert posted(client_a, supplier, [line(rice)])["purchase_no"].endswith("/0001")

    def test_each_line_writes_one_positive_purchase_row_to_the_ledger(self, client_a, setup, fresh):
        supplier, rice, dal = setup
        purchase = posted(client_a, supplier, [line(rice, "100", "20", discount="100"), line(dal, "8", "60")])

        rows = fresh(
            lambda s: (
                s.execute(select(InventoryTransaction).order_by(InventoryTransaction.id)).scalars().all()
            )
        )

        assert [(r.txn_type.value, r.qty_delta, r.reference_type.value) for r in rows] == [
            ("PURCHASE", Decimal("100.000"), "PURCHASE_ITEM"),
            ("PURCHASE", Decimal("8.000"), "PURCHASE_ITEM"),
        ]
        assert [r.reference_id for r in rows] == [i["id"] for i in purchase["items"]]
        assert rows[0].unit_cost == Decimal("19.00")  # net of the 100 discount: 1900 / 100
        assert all(r.txn_date == today_in_shop_timezone() for r in rows)

    def test_a_purchase_is_dated_in_the_ledger_with_its_own_date(self, client_a, setup, fresh):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice)], purchase_date="2026-02-03")

        assert fresh(lambda s: s.scalar(select(InventoryTransaction.txn_date))).isoformat() == "2026-02-03"

    def test_the_discount_lowers_the_average_cost(self, client_a, setup):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "10", "100", discount="100")])  # 900 for 10 -> 90 each
        assert avg_cost(client_a, rice) == Decimal("90.00")

    def test_a_purchase_after_opening_stock_averages_with_it(self, client_a, setup):
        supplier, rice, _ = setup
        client_a.post(
            f"{INV}/opening-stock", json={"product_id": rice["id"], "quantity": "10", "unit_cost": "10"}
        )

        posted(client_a, supplier, [line(rice, "10", "20")])

        assert stock(client_a, rice) == 20 and avg_cost(client_a, rice) == Decimal("15.00")

    def test_opening_stock_without_a_cost_keeps_the_cost_unknown_after_a_purchase(self, client_a, setup):
        supplier, rice, _ = setup
        client_a.post(f"{INV}/opening-stock", json={"product_id": rice["id"], "quantity": "10"})

        posted(client_a, supplier, [line(rice, "10", "20")])

        assert stock(client_a, rice) == 20 and avg_cost(client_a, rice) is None  # never a made-up number

    def test_fractional_quantities_and_paise_precision(self, client_a, tenant_a, units, setup):
        supplier, *_ = setup
        kg = make_product(client_a, tenant_a, units, "SUGAR", unit="kg")

        posted(client_a, supplier, [line(kg, "2.5", "40")])
        posted(client_a, supplier, [line(kg, "1.25", "50")])

        assert stock(client_a, kg) == Decimal("3.75") and avg_cost(client_a, kg) == Decimal("43.33")

    def test_a_purchase_cannot_be_posted_twice(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20")])
        assert post(client_a, purchase).status_code == 200

        again = post(client_a, purchase)

        assert again.status_code == 409 and "already been posted" in again.json()["detail"][0]["msg"]
        assert stock(client_a, rice) == 10  # not 20

    def test_a_posted_purchase_cannot_be_edited(self, client_a, setup):
        supplier, rice, dal = setup
        purchase = posted(client_a, supplier, [line(rice)])
        item_id = purchase["items"][0]["id"]
        url = f"{API}/{purchase['id']}"

        attempts = [
            client_a.patch(url, json={"notes": "x"}),
            client_a.put(f"{url}/items", json={"items": [line(dal)]}),
            client_a.post(f"{url}/items", json=line(dal)),
            client_a.patch(f"{url}/items/{item_id}", json={"quantity": "99"}),
        ]

        assert [a.status_code for a in attempts] == [409, 409, 409, 409]
        assert client_a.get(url).json()["items"][0]["quantity"] == "10.000"

    def test_the_detail_shows_the_inventory_effect_of_each_line(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])

        effects = client_a.get(f"{API}/{purchase['id']}").json()["items"][0]["inventory_effects"]

        assert [(e["txn_type"], e["qty_delta"]) for e in effects] == [("PURCHASE", "10.000")]

    def test_the_products_history_shows_the_purchase_and_links_to_it(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])

        rows = client_a.get(f"{INV}/products/{rice['id']}/transactions").json()["items"]

        assert rows[0]["txn_type"] == "PURCHASE" and rows[0]["qty_delta"] == "10.000"
        assert rows[0]["purchase_id"] == purchase["id"] and rows[0]["purchase_no"] == purchase["purchase_no"]
        assert rows[0]["balance_after"] == "10.000"

    def test_posting_an_inactive_product_is_refused_and_changes_nothing(self, client_a, setup):
        supplier, rice, dal = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20"), line(dal, "5", "10")])
        client_a.post(f"{PRODUCTS}/{dal['id']}/deactivate")

        response = post(client_a, purchase)

        assert response.status_code == 409
        assert stock(client_a, rice) == 0 and stock(client_a, dal) == 0
        assert client_a.get(f"{API}/{purchase['id']}").json()["status"] == "DRAFT"

    def test_the_same_product_may_appear_on_two_lines(self, client_a, setup):
        supplier, rice, _ = setup

        posted(client_a, supplier, [line(rice, "10", "10"), line(rice, "10", "20")])

        assert stock(client_a, rice) == 20 and avg_cost(client_a, rice) == Decimal("15.00")

    def test_posting_is_audited_with_before_and_after(self, client_a, setup, fresh):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])

        entry = fresh(
            lambda s: s.execute(
                select(AuditLog).where(AuditLog.entity_type == "purchase", AuditLog.action == "post")
            ).scalar_one()
        )

        assert entry.entity_id == purchase["id"]
        assert entry.before_json["status"] == "DRAFT" and entry.after_json["status"] == "POSTED"
        assert entry.after_json["purchase_no"] == purchase["purchase_no"]
        assert entry.after_json["total_amount"] == "200.00"


class TestVoid:
    def test_voiding_a_posted_purchase_reverses_the_stock_and_keeps_the_record(self, client_a, setup, fresh):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])

        response = client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "Wrong supplier"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "VOID" and data["void_reason"] == "Wrong supplier" and data["voided_at"]
        assert data["purchase_no"] == purchase["purchase_no"]  # the number is never reused
        assert stock(client_a, rice) == 0
        assert [(e["txn_type"], e["qty_delta"]) for e in data["items"][0]["inventory_effects"]] == [
            ("PURCHASE", "10.000"),
            ("REVERSAL", "-10.000"),
        ]
        # nothing was deleted: both ledger rows are still there
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == 2

    def test_a_void_puts_the_average_cost_back_as_if_the_purchase_never_happened(self, client_a, setup):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "100", "20")])
        second = posted(client_a, supplier, [line(rice, "50", "30")])
        assert avg_cost(client_a, rice) == Decimal("23.33")

        client_a.post(f"{API}/{second['id']}/void", json={"reason": "Mistake"})

        assert stock(client_a, rice) == 100 and avg_cost(client_a, rice) == Decimal("20.00")

    def test_voiding_an_older_purchase_recomputes_from_the_remaining_history(self, client_a, setup):
        supplier, rice, _ = setup
        first = posted(client_a, supplier, [line(rice, "100", "20")])
        posted(client_a, supplier, [line(rice, "50", "30")])

        client_a.post(f"{API}/{first['id']}/void", json={"reason": "Mistake"})

        assert stock(client_a, rice) == 50 and avg_cost(client_a, rice) == Decimal("30.00")

    def test_voiding_the_only_purchase_leaves_the_cost_unknown_again(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "Mistake"})
        assert avg_cost(client_a, rice) is None

    def test_a_reason_is_required(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        url = f"{API}/{purchase['id']}/void"

        assert client_a.post(url, json={}).status_code == 422
        assert client_a.post(url, json={"reason": "   "}).status_code == 422
        assert client_a.post(url, json={"reason": "x" * 501}).status_code == 422
        assert stock(client_a, rice) == 10

    def test_a_void_purchase_cannot_be_voided_or_posted_again(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "x"})

        assert client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "again"}).status_code == 409
        assert post(client_a, purchase).status_code == 409
        assert stock(client_a, rice) == 0  # only one reversal

    def test_voiding_a_draft_discards_it_without_touching_stock_or_using_a_number(
        self, client_a, setup, fresh
    ):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)])

        response = client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "Not needed"})

        assert response.status_code == 200
        assert response.json()["status"] == "VOID" and response.json()["purchase_no"] is None
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == 0
        assert posted(client_a, supplier, [line(rice)])["purchase_no"].endswith("/0001")

    def test_voiding_is_audited(self, client_a, setup, fresh):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "Wrong supplier"})

        entry = fresh(lambda s: s.execute(select(AuditLog).where(AuditLog.action == "void")).scalar_one())

        assert entry.before_json["status"] == "POSTED" and entry.after_json["status"] == "VOID"
        assert entry.after_json["void_reason"] == "Wrong supplier"


class TestCorrect:
    def test_a_voided_purchase_can_be_copied_into_a_new_draft(self, client_a, setup):
        supplier, rice, dal = setup
        original = posted(
            client_a,
            supplier,
            [line(rice, "10", "20"), line(dal, "2", "5", discount="1")],
            supplier_invoice_no="INV-1",
            notes="First try",
            purchase_date="2026-03-01",
        )
        client_a.post(f"{API}/{original['id']}/void", json={"reason": "Wrong quantity"})

        response = client_a.post(f"{API}/{original['id']}/correct")

        assert response.status_code == 201
        copy = response.json()
        assert copy["status"] == "DRAFT" and copy["purchase_no"] is None
        assert copy["replaces_id"] == original["id"]
        assert copy["supplier_invoice_no"] == "INV-1" and copy["notes"] == "First try"
        assert copy["purchase_date"] == "2026-03-01"
        assert [(i["sku"], i["quantity"], i["unit_cost"], i["discount"]) for i in copy["items"]] == [
            ("RICE", "10.000", "20.00", "0.00"),
            ("DAL", "2.000", "5.00", "1.00"),
        ]
        assert client_a.get(f"{API}/{original['id']}").json()["replaced_by_id"] == copy["id"]

    def test_the_corrected_copy_can_be_edited_and_posted_with_the_same_invoice_number(self, client_a, setup):
        supplier, rice, _ = setup
        original = posted(client_a, supplier, [line(rice, "10", "20")], supplier_invoice_no="INV-1")
        client_a.post(f"{API}/{original['id']}/void", json={"reason": "Wrong quantity"})
        copy = client_a.post(f"{API}/{original['id']}/correct").json()
        client_a.patch(f"{API}/{copy['id']}/items/{copy['items'][0]['id']}", json={"quantity": "12"})

        result = post(client_a, copy)

        assert result.status_code == 200
        assert stock(client_a, rice) == 12 and avg_cost(client_a, rice) == Decimal("20.00")
        assert result.json()["purchase_no"] != original["purchase_no"]

    def test_only_a_voided_purchase_can_be_corrected(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        assert client_a.post(f"{API}/{purchase['id']}/correct").status_code == 409

    def test_a_voided_purchase_can_be_corrected_once(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{API}/{purchase['id']}/correct").status_code == 201
        assert client_a.post(f"{API}/{purchase['id']}/correct").status_code == 409

    def test_a_discarded_draft_cannot_be_corrected(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)])
        client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{API}/{purchase['id']}/correct").status_code == 409


class TestListAndSearch:
    @pytest.fixture
    def three(self, client_a, setup):
        supplier, rice, dal = setup
        other = make_supplier(client_a, "Gupta Stores")
        a = posted(
            client_a,
            supplier,
            [line(rice, "10", "20")],
            supplier_invoice_no="A-100",
            purchase_date="2026-01-10",
        )
        b = posted(
            client_a,
            other,
            [line(dal, "5", "30"), line(rice, "1", "1")],
            supplier_invoice_no="B-200",
            purchase_date="2026-02-10",
        )
        c = draft(
            client_a, supplier, [line(dal, "1", "1")], purchase_date="2026-03-10", notes="festival order"
        )
        return supplier, other, a, b, c

    def ids(self, client, **params):
        response = client.get(API, params=params)
        assert response.status_code == 200, response.text
        return [p["id"] for p in response.json()["items"]]

    def test_newest_first_with_totals_and_counts(self, client_a, three):
        _, _, a, b, c = three

        data = client_a.get(API).json()

        assert [p["id"] for p in data["items"]] == [c["id"], b["id"], a["id"]] and data["total"] == 3
        row = data["items"][1]
        assert row["item_count"] == 2 and row["total_amount"] == "151.00"
        assert row["supplier_name"] == "Gupta Stores" and row["created_by_name"] == "Test Owner"
        assert "items" not in row

    def test_search_by_purchase_number_invoice_supplier_or_notes(self, client_a, three):
        _, _, a, b, c = three

        assert self.ids(client_a, q=a["purchase_no"]) == [a["id"]]
        assert self.ids(client_a, q="b-200") == [b["id"]]
        assert self.ids(client_a, q="gupta") == [b["id"]]
        assert self.ids(client_a, q="festival") == [c["id"]]
        assert self.ids(client_a, q="nothing-like-this") == []

    def test_search_treats_percent_and_underscore_literally(self, client_a, three):
        assert self.ids(client_a, q="%") == []
        assert self.ids(client_a, q="_") == []

    def test_filter_by_supplier(self, client_a, three):
        supplier, other, a, b, c = three
        assert self.ids(client_a, supplier_id=other["id"]) == [b["id"]]
        assert self.ids(client_a, supplier_id=supplier["id"]) == [c["id"], a["id"]]

    def test_filter_by_status_one_or_several(self, client_a, three):
        _, _, a, b, c = three
        assert self.ids(client_a, status="DRAFT") == [c["id"]]
        assert self.ids(client_a, status=["DRAFT", "POSTED"]) == [c["id"], b["id"], a["id"]]
        client_a.post(f"{API}/{a['id']}/void", json={"reason": "x"})
        assert self.ids(client_a, status="VOID") == [a["id"]]
        assert client_a.get(API, params={"status": "BOGUS"}).status_code == 422

    def test_filter_by_date_range_inclusive(self, client_a, three):
        _, _, a, b, c = three
        assert self.ids(client_a, date_from="2026-02-10") == [c["id"], b["id"]]
        assert self.ids(client_a, date_to="2026-02-10") == [b["id"], a["id"]]
        assert self.ids(client_a, date_from="2026-02-10", date_to="2026-02-10") == [b["id"]]
        assert (
            client_a.get(API, params={"date_from": "2026-05-01", "date_to": "2026-01-01"}).status_code == 422
        )

    def test_filters_combine(self, client_a, three):
        supplier, _, a, _, c = three
        assert self.ids(client_a, supplier_id=supplier["id"], status="POSTED") == [a["id"]]

    def test_pagination(self, client_a, three):
        _, _, a, b, c = three
        page = client_a.get(API, params={"limit": 2, "offset": 1}).json()
        assert [p["id"] for p in page["items"]] == [b["id"], a["id"]] and page["total"] == 3
        assert client_a.get(API, params={"limit": 0}).status_code == 422
        assert client_a.get(API, params={"limit": 201}).status_code == 422

    def test_supplier_purchase_totals_count_only_posted_purchases(self, client_a, three):
        supplier, _, a, _, _ = three  # supplier has one posted (200.00) and one draft
        totals = client_a.get(f"{API}/supplier-totals/{supplier['id']}").json()
        assert totals == {"posted_count": 1, "posted_total": "200.00"}
        client_a.post(f"{API}/{a['id']}/void", json={"reason": "x"})
        assert client_a.get(f"{API}/supplier-totals/{supplier['id']}").json() == {
            "posted_count": 0,
            "posted_total": "0.00",
        }


class TestTenantIsolation:
    def test_another_shops_purchase_is_not_found_by_any_endpoint(self, client_a, client_b, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)])
        url = f"{API}/{purchase['id']}"
        item_id = purchase["items"][0]["id"]

        responses = [
            client_b.get(url),
            client_b.patch(url, json={"notes": "x"}),
            client_b.put(f"{url}/items", json={"items": []}),
            client_b.post(f"{url}/items", json={}),
            client_b.patch(f"{url}/items/{item_id}", json={}),
            client_b.post(f"{url}/post"),
            client_b.post(f"{url}/void", json={"reason": "x"}),
            client_b.post(f"{url}/correct"),
        ]

        assert [r.status_code for r in responses[:1] + responses[5:]] == [404, 404, 404, 404]
        assert client_a.get(url).json()["status"] == "POSTED"

    def test_lists_never_show_another_shops_purchases(self, client_a, client_b, setup):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice)])
        assert client_b.get(API).json()["total"] == 0

    def test_another_shops_supplier_or_product_cannot_be_used(
        self, client_a, client_b, tenant_b, units, setup
    ):
        supplier, rice, _ = setup
        theirs = make_supplier(client_b, "Their Supplier")
        their_product = make_product(client_b, tenant_b, units, "THEIRS")

        as_supplier = client_a.post(API, json={"supplier_id": theirs["id"]})
        as_product = client_a.post(API, json={"supplier_id": supplier["id"], "items": [line(their_product)]})

        assert as_supplier.status_code == 422 and "supplier_id" in errors(as_supplier)
        assert as_product.status_code == 422 and "items.0.product_id" in errors(as_product)

    def test_shops_have_independent_numbering_and_stock(self, client_a, client_b, tenant_b, units, setup):
        supplier, rice, _ = setup
        their_supplier = make_supplier(client_b, "Their Supplier")
        their_product = make_product(client_b, tenant_b, units, "RICE")

        mine = posted(client_a, supplier, [line(rice, "10", "20")])
        theirs = posted(client_b, their_supplier, [line(their_product, "3", "9")])

        assert mine["purchase_no"] == theirs["purchase_no"]  # both are 0001 of their own shop
        assert stock(client_a, rice) == 10 and stock(client_b, their_product) == 3

    def test_totals_of_another_shops_supplier_are_zero_not_an_error_leak(self, client_a, client_b, setup):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice)])
        assert client_b.get(f"{API}/supplier-totals/{supplier['id']}").json()["posted_count"] == 0


class TestEveryBusinessType:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_a_purchase_works_the_same_for_every_business_type(
        self, make_client, tenant_of, units, business_type
    ):
        tenant = tenant_of(business_type)
        client = make_client(tenant)
        supplier = make_supplier(client)
        product = make_product(client, tenant, units, "ITEM", unit="kg")

        first = posted(client, supplier, [line(product, "100", "20")])
        posted(client, supplier, [line(product, "50", "30")])

        assert first["status"] == "POSTED"
        assert stock(client, product) == 150 and avg_cost(client, product) == Decimal("23.33")


class TestRowsInTheDatabase:
    def test_posting_stores_exactly_the_rows_and_no_stock_column(self, client_a, setup, fresh):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "10", "20")])

        counts = fresh(
            lambda s: (
                s.scalar(select(func.count()).select_from(Purchase)),
                s.scalar(select(func.count()).select_from(PurchaseItem)),
                s.scalar(select(func.count()).select_from(InventoryTransaction)),
            )
        )

        assert counts == (1, 1, 1)
