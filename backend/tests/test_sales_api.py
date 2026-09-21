"""Detailed Sales API: cart, calculation, posting, stock, cost and profit, khata, void, isolation."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, CustomerLedgerEntry, InventoryTransaction, Sale, SaleItem
from tests.factories import today_in_shop_timezone
from tests.test_business_types import REQUESTED_TYPES
from tests.test_purchases_api import avg_cost, errors, make_product, make_supplier, stock
from tests.test_purchases_api import line as buy_line
from tests.test_purchases_api import posted as bought

API = "/api/v1/sales"
CUSTOMERS = "/api/v1/customers"
INV = "/api/v1/inventory"
PRODUCTS = "/api/v1/products"


def item(product, quantity="1", **extra):
    return {"product_id": product["id"], "quantity": quantity, **extra}


def draft(client, items=None, **header):
    response = client.post(API, json={"items": items or [], **header})
    assert response.status_code == 201, response.text
    return response.json()


def post(client, sale, **payment):
    """Post with a payment. Cash unless said otherwise (a method is required whenever money is received)."""
    payment.setdefault("payment_method", "CASH")
    return client.post(f"{API}/{sale['id']}/post", json=payment)


def post_bare(client, sale):
    """Post with no body at all: the sale is paid in full, but no method is given."""
    return client.post(f"{API}/{sale['id']}/post")


def sold(client, items, *, header=None, **payment):
    payment.setdefault("payment_method", "CASH")  # a method is required whenever money is received
    response = post(client, draft(client, items, **(header or {})), **payment)
    assert response.status_code == 200, response.text
    return response.json()


def make_customer(client, name="Ramesh", **extra):
    response = client.post(CUSTOMERS, json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()["customer"]


def owed(client, customer) -> Decimal:
    return Decimal(client.get(f"{CUSTOMERS}/{customer['id']}/balance").json()["balance"])


def stock_up(client, supplier, product, quantity, price):
    bought(client, supplier, [buy_line(product, str(quantity), str(price))])


@pytest.fixture
def shelf(client_a, tenant_a, units):
    """A supplier and three products with stock: rice (10 pcs @ 20), sugar (150 kg @ 23.33 average), oil (no cost)."""
    supplier = make_supplier(client_a)
    rice = make_product(client_a, tenant_a, units, "RICE", selling_price="50")
    sugar = make_product(client_a, tenant_a, units, "SUGAR", unit="kg", selling_price="48")
    oil = make_product(client_a, tenant_a, units, "OIL", unit="L", selling_price="140")
    stock_up(client_a, supplier, rice, 10, 20)
    stock_up(client_a, supplier, sugar, 100, 20)
    stock_up(client_a, supplier, sugar, 50, 30)  # 150 kg at an average of 23.33
    client_a.post(f"{INV}/opening-stock", json={"product_id": oil["id"], "quantity": "20"})  # cost unknown
    return {"supplier": supplier, "rice": rice, "sugar": sugar, "oil": oil}


class TestDraft:
    def test_a_draft_is_a_cart_that_changes_nothing(self, client_a, shelf):
        rice = shelf["rice"]

        sale = draft(client_a, [item(rice, "3")])

        assert sale["status"] == "DRAFT" and sale["invoice_no"] is None
        assert sale["payment_type"] is None and sale["amount_paid"] is None
        assert (sale["subtotal"], sale["discount"], sale["total_amount"]) == ("150.00", "0.00", "150.00")
        assert sale["cogs_total"] is None and sale["gross_profit"] is None  # no cost until posted
        assert stock(client_a, rice) == 10 and avg_cost(client_a, rice) == Decimal("20.00")

    def test_the_product_price_is_the_default_and_can_be_overridden(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2"), item(shelf["rice"], "1", unit_price="45")])
        assert [i["unit_price"] for i in sale["items"]] == ["50.00", "45.00"]
        assert sale["subtotal"] == "145.00"

    def test_line_and_bill_discounts(self, client_a, shelf):
        sale = draft(
            client_a,
            [item(shelf["rice"], "3", discount="10"), item(shelf["sugar"], "2.5", unit_price="48")],
            discount="15",
        )

        rice, sugar = sale["items"]
        assert (rice["gross"], rice["discount"], rice["line_total"]) == ("150.00", "10.00", "140.00")
        assert sugar["line_total"] == "120.00"
        assert (sale["subtotal"], sale["discount"], sale["total_amount"]) == ("260.00", "15.00", "245.00")

    def test_a_line_is_rounded_to_whole_paise_half_up(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["sugar"], "0.5", unit_price="0.01")])
        assert sale["items"][0]["line_total"] == "0.01"  # 0.005 rounds up

    def test_an_empty_cart_can_be_started_and_filled_later(self, client_a, shelf):
        sale = draft(client_a)
        filled = client_a.put(f"{API}/{sale['id']}/items", json={"items": [item(shelf["rice"], "2")]})
        assert filled.status_code == 200 and filled.json()["total_amount"] == "100.00"

    def test_lines_can_be_changed_and_removed(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2"), item(shelf["sugar"], "1")])

        changed = client_a.put(f"{API}/{sale['id']}/items", json={"items": [item(shelf["rice"], "4")]}).json()

        assert [(i["sku"], i["quantity"]) for i in changed["items"]] == [("RICE", "4.000")]
        assert changed["total_amount"] == "200.00"

    def test_header_can_be_edited(self, client_a, shelf):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"], "2")])

        edited = client_a.patch(
            f"{API}/{sale['id']}", json={"customer_id": c["id"], "notes": " Deliver ", "discount": "10"}
        ).json()

        assert edited["customer_name"] == "Ramesh" and edited["notes"] == "Deliver"
        assert edited["total_amount"] == "90.00"

    def test_a_bill_discount_cannot_exceed_the_items(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "1")])
        response = client_a.patch(f"{API}/{sale['id']}", json={"discount": "51"})
        assert response.status_code == 422 and "discount" in errors(response)

    def test_removing_lines_below_the_bill_discount_is_refused(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2")], discount="80")
        response = client_a.put(f"{API}/{sale['id']}/items", json={"items": [item(shelf["rice"], "1")]})
        assert response.status_code == 422 and "discount" in errors(response)

    def test_there_is_no_delete(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"])])
        assert client_a.delete(f"{API}/{sale['id']}").status_code == 405


class TestValidation:
    def bad(self, client, items, **header):
        return client.post(API, json={"items": items, **header})

    def test_unknown_inactive_and_other_shops_products_are_refused(
        self, client_a, client_b, tenant_b, units, shelf
    ):
        theirs = make_product(client_b, tenant_b, units, "THEIRS")
        client_a.post(f"{PRODUCTS}/{shelf['oil']['id']}/deactivate")

        unknown = self.bad(client_a, [{"product_id": 99999, "quantity": "1"}])
        foreign = self.bad(client_a, [item(theirs)])
        inactive = self.bad(client_a, [item(shelf["oil"])])

        assert all(r.status_code == 422 for r in (unknown, foreign, inactive))
        assert "items.0.product_id" in errors(unknown) and "items.0.product_id" in errors(foreign)
        assert "inactive" in errors(inactive)["items.0.product_id"]

    @pytest.mark.parametrize("quantity", ["0", "0.000", "-1", "abc", "", "1.2345"])
    def test_bad_quantities_are_refused(self, client_a, shelf, quantity):
        assert self.bad(client_a, [item(shelf["rice"], quantity)]).status_code == 422

    def test_fractions_follow_the_unit(self, client_a, shelf):
        refused = self.bad(client_a, [item(shelf["rice"], "2.5")])
        assert refused.status_code == 422 and "cannot be split" in errors(refused)["items.0.quantity"]
        assert draft(client_a, [item(shelf["sugar"], "2.5")])["items"][0]["quantity"] == "2.500"

    @pytest.mark.parametrize("price", ["-1", "abc", "1.005"])
    def test_bad_prices_are_refused(self, client_a, shelf, price):
        assert self.bad(client_a, [item(shelf["rice"], "1", unit_price=price)]).status_code == 422

    def test_amounts_must_be_text_not_numbers(self, client_a, shelf):
        assert (
            self.bad(
                client_a, [{"product_id": shelf["rice"]["id"], "quantity": 1, "unit_price": 12.5}]
            ).status_code
            == 422
        )

    def test_a_discount_cannot_make_a_line_negative(self, client_a, shelf):
        refused = self.bad(client_a, [item(shelf["rice"], "1", discount="50.01")])
        assert refused.status_code == 422 and "items.0.discount" in errors(refused)
        assert (
            draft(client_a, [item(shelf["rice"], "1", discount="50")])["total_amount"] == "0.00"
        )  # free is allowed

    def test_every_bad_line_is_reported_at_once_with_its_position(self, client_a, shelf):
        response = self.bad(
            client_a,
            [item(shelf["rice"]), item(shelf["rice"], "2.5"), {"product_id": 99999, "quantity": "1"}],
        )
        assert set(errors(response)) == {"items.1.quantity", "items.2.product_id"}

    def test_a_future_date_is_refused(self, client_a, shelf):
        tomorrow = (today_in_shop_timezone() + timedelta(days=1)).isoformat()
        assert self.bad(client_a, [item(shelf["rice"])], sale_date=tomorrow).status_code == 422

    def test_the_customer_must_belong_to_the_shop(self, client_a, client_b, shelf):
        theirs = make_customer(client_b, "Theirs")
        response = self.bad(client_a, [item(shelf["rice"])], customer_id=theirs["id"])
        assert response.status_code == 422 and "customer_id" in errors(response)

    def test_unknown_fields_and_client_supplied_totals_are_refused(self, client_a, shelf):
        assert self.bad(client_a, [item(shelf["rice"])], total_amount="1").status_code == 422
        assert self.bad(client_a, [item(shelf["rice"], line_total="1")]).status_code == 422
        assert self.bad(client_a, [item(shelf["rice"])], status="POSTED").status_code == 422

    def test_a_sale_has_a_limit_on_lines(self, client_a, shelf):
        assert self.bad(client_a, [item(shelf["rice"])] * 201).status_code == 422

    def test_selling_above_mrp_warns_by_default(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units, "PACK", mrp="100", selling_price="90")

        sale = draft(client_a, [item(product, "1", unit_price="120")])

        assert sale["items"][0]["mrp"] == "100.00" and "above its MRP" in sale["warnings"][0]

    def test_selling_above_mrp_is_refused_when_the_shop_blocks_it(self, client_a, tenant_a, units, set_shop):
        product = make_product(client_a, tenant_a, units, "PACK", mrp="100", selling_price="90")
        set_shop(tenant_a, mrp_validation_mode="BLOCK")

        response = self.bad(client_a, [item(product, "1", unit_price="100.01")])

        assert response.status_code == 422 and "MRP" in errors(response)["items.0.unit_price"]
        assert draft(client_a, [item(product, "1", unit_price="100")])["warnings"] == []

    def test_posting_needs_at_least_one_item(self, client_a):
        response = post(client_a, draft(client_a))
        assert response.status_code == 422 and "items" in errors(response)


class TestCalculate:
    def test_it_prices_a_cart_without_saving_anything(self, client_a, shelf, fresh):
        response = client_a.post(
            f"{API}/calculate",
            json={
                "items": [item(shelf["rice"], "3", discount="10"), item(shelf["sugar"], "2")],
                "discount": "6",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert [ln["line_total"] for ln in data["lines"]] == ["140.00", "96.00"]
        assert (data["subtotal"], data["discount"], data["total"]) == ("236.00", "6.00", "230.00")
        assert data["errors"] == [] and [ln["gross"] for ln in data["lines"]] == ["150.00", "96.00"]
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Sale))) == 0

    def test_it_reports_stock_and_flags_short_lines(self, client_a, shelf):
        data = client_a.post(
            f"{API}/calculate", json={"items": [item(shelf["rice"], "6"), item(shelf["rice"], "6")]}
        ).json()
        assert [(ln["available"], ln["short"]) for ln in data["lines"]] == [
            ("10.000", True),
            ("10.000", True),
        ]

    def test_it_reports_problems_per_line_instead_of_failing(self, client_a, shelf):
        data = client_a.post(
            f"{API}/calculate",
            json={
                "items": [
                    item(shelf["rice"], "1"),
                    item(shelf["rice"], "2.5"),
                    item(shelf["rice"], "1", discount="99"),
                ]
            },
        ).json()

        assert data["lines"][0]["errors"] == [] and data["lines"][0]["line_total"] == "50.00"
        assert data["lines"][1]["errors"][0]["field"] == "quantity" and data["lines"][1]["line_total"] is None
        assert data["lines"][2]["errors"][0]["field"] == "discount"
        assert data["subtotal"] == "50.00"  # only the valid line counts

    def test_a_too_large_bill_discount_is_reported(self, client_a, shelf):
        data = client_a.post(
            f"{API}/calculate", json={"items": [item(shelf["rice"], "1")], "discount": "60"}
        ).json()
        assert data["errors"][0]["field"] == "discount" and data["total"] == "50.00"

    def test_an_empty_cart_is_zero(self, client_a):
        data = client_a.post(f"{API}/calculate", json={"items": []}).json()
        assert (data["subtotal"], data["total"], data["lines"]) == ("0.00", "0.00", [])

    def test_it_uses_only_this_shops_products(self, client_a, client_b, tenant_b, units):
        theirs = make_product(client_b, tenant_b, units, "THEIRS")
        data = client_a.post(f"{API}/calculate", json={"items": [item(theirs)]}).json()
        assert data["lines"][0]["errors"][0]["message"] == "Product not found."


class TestPostingAndStock:
    def test_posting_numbers_the_sale_and_takes_the_stock_out(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "7")])

        year = today_in_shop_timezone()
        start = year.year if year.month >= 4 else year.year - 1
        assert (
            sale["status"] == "POSTED" and sale["invoice_no"] == f"INV/{start}-{(start + 1) % 100:02d}/0001"
        )
        assert sale["posted_at"] and sale["posted_by_name"] == "Test Owner"
        assert stock(client_a, shelf["rice"]) == 3

    def test_available_stock_is_enough_but_one_more_is_not(self, client_a, shelf, fresh):
        rice = shelf["rice"]
        assert post(client_a, draft(client_a, [item(rice, "10")])).status_code == 200  # all of it
        assert stock(client_a, rice) == 0

        refused = post(client_a, draft(client_a, [item(rice, "1")]))

        assert refused.status_code == 409 and "Not enough stock" in errors(refused)["items.0.quantity"]
        assert stock(client_a, rice) == 0  # never negative

    def test_selling_11_of_10_is_refused_and_writes_nothing(self, client_a, shelf, fresh):
        sale = draft(client_a, [item(shelf["rice"], "11")])
        before = fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction)))

        response = post(client_a, sale)

        assert response.status_code == 409 and "10" in errors(response)["items.0.quantity"]
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == before
        assert client_a.get(f"{API}/{sale['id']}").json()["status"] == "DRAFT"
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_two_lines_of_one_product_are_checked_together(self, client_a, shelf):
        response = post(client_a, draft(client_a, [item(shelf["rice"], "6"), item(shelf["rice"], "6")]))
        assert response.status_code == 409 and stock(client_a, shelf["rice"]) == 10

    def test_every_short_line_is_reported_at_once(self, client_a, shelf):
        sale = draft(
            client_a, [item(shelf["rice"], "11"), item(shelf["sugar"], "1"), item(shelf["oil"], "21")]
        )
        response = post(client_a, sale)
        assert set(errors(response)) == {"items.0.quantity", "items.2.quantity"}

    def test_a_sale_of_several_products_is_all_or_nothing(self, client_a, shelf, fresh):
        sale = draft(client_a, [item(shelf["rice"], "5"), item(shelf["sugar"], "2"), item(shelf["oil"], "1")])
        assert post(client_a, sale).status_code == 200
        assert (
            stock(client_a, shelf["rice"]),
            stock(client_a, shelf["sugar"]),
            stock(client_a, shelf["oil"]),
        ) == (5, 148, 19)

        short = draft(
            client_a, [item(shelf["rice"], "1"), item(shelf["sugar"], "1"), item(shelf["oil"], "99")]
        )
        assert post(client_a, short).status_code == 409
        assert (
            stock(client_a, shelf["rice"]),
            stock(client_a, shelf["sugar"]),
            stock(client_a, shelf["oil"]),
        ) == (5, 148, 19)

    def test_the_ledger_gets_one_negative_sale_row_per_line(self, client_a, shelf, fresh):
        sale = sold(client_a, [item(shelf["rice"], "3"), item(shelf["sugar"], "2.5")])

        rows = fresh(
            lambda s: s.scalars(
                select(InventoryTransaction)
                .where(InventoryTransaction.txn_type == "SALE")
                .order_by(InventoryTransaction.id)
            ).all()
        )

        assert [(r.qty_delta, r.reference_type.value) for r in rows] == [
            (Decimal("-3.000"), "SALE_ITEM"),
            (Decimal("-2.500"), "SALE_ITEM"),
        ]
        assert [r.reference_id for r in rows] == [i["id"] for i in sale["items"]]
        assert all(r.txn_date == today_in_shop_timezone() for r in rows)

    def test_stock_is_derived_from_the_ledger_only(self, client_a, shelf):
        sold(client_a, [item(shelf["rice"], "4")])
        history = client_a.get(f"{INV}/products/{shelf['rice']['id']}/transactions").json()["items"]
        assert [(r["txn_type"], r["qty_delta"], r["balance_after"]) for r in history][:1] == [
            ("SALE", "-4.000", "6.000")
        ]

    def test_a_sale_never_changes_the_average_cost(self, client_a, shelf):
        sold(client_a, [item(shelf["sugar"], "10")])
        assert avg_cost(client_a, shelf["sugar"]) == Decimal("23.33")

    def test_a_shop_that_allows_negative_stock_can_oversell(self, client_a, tenant_a, shelf, set_shop):
        set_shop(tenant_a, allow_negative_stock=True)
        sold(client_a, [item(shelf["rice"], "12")])
        assert stock(client_a, shelf["rice"]) == -2

    def test_a_purchase_whose_stock_was_sold_cannot_be_voided(self, client_a, shelf):
        purchase = bought(client_a, shelf["supplier"], [buy_line(shelf["rice"], "5", "20")])
        sold(client_a, [item(shelf["rice"], "14")])  # 15 in stock, 14 sold: 1 left, purchase brought 5

        response = client_a.post(f"/api/v1/purchases/{purchase['id']}/void", json={"reason": "Mistake"})

        assert response.status_code == 409 and stock(client_a, shelf["rice"]) == 1

    def test_the_products_history_and_the_sale_link_up(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")])
        row = client_a.get(f"{INV}/products/{shelf['rice']['id']}/transactions").json()["items"][0]
        assert row["sale_id"] == sale["id"] and row["sale_no"] == sale["invoice_no"]

    def test_sale_numbers_run_in_sequence_and_only_posting_uses_one(self, client_a, shelf):
        draft(client_a, [item(shelf["rice"])])
        numbers = [sold(client_a, [item(shelf["rice"])])["invoice_no"] for _ in range(3)]
        assert [n.rsplit("/", 1)[1] for n in numbers] == ["0001", "0002", "0003"]

    def test_the_sale_date_is_the_ledger_date(self, client_a, shelf, fresh):
        sold(client_a, [item(shelf["rice"])], header={"sale_date": "2026-02-03"})
        row = fresh(
            lambda s: s.scalars(
                select(InventoryTransaction).where(InventoryTransaction.txn_type == "SALE")
            ).one()
        )
        assert row.txn_date.isoformat() == "2026-02-03"


class TestCostAndProfit:
    def test_cogs_uses_the_average_cost_at_the_time_of_sale(self, client_a, shelf):
        # 150 kg at an average of 23.33; 5 kg sold at 48 -> cost 116.65, revenue 240, profit 123.35
        sale = sold(client_a, [item(shelf["sugar"], "5")])

        line = sale["items"][0]
        assert (line["unit_cost"], line["cogs_amount"], line["line_total"], line["profit"]) == (
            "23.33",
            "116.65",
            "240.00",
            "123.35",
        )
        assert (sale["cogs_total"], sale["gross_profit"], sale["lines_without_cost"]) == (
            "116.65",
            "123.35",
            0,
        )

    def test_the_cost_snapshot_does_not_change_when_the_average_changes_later(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["sugar"], "5")])
        stock_up(client_a, shelf["supplier"], shelf["sugar"], 100, 100)  # the average jumps

        again = client_a.get(f"{API}/{sale['id']}").json()

        assert again["items"][0]["cogs_amount"] == "116.65" and again["gross_profit"] == "123.35"

    def test_unknown_cost_stays_null_and_profit_is_not_available(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["oil"], "2")])  # opening stock without a cost

        line = sale["items"][0]
        assert (line["unit_cost"], line["cogs_amount"], line["profit"]) == (None, None, None)
        assert (sale["cogs_total"], sale["gross_profit"], sale["lines_without_cost"]) == (None, None, 1)
        assert sale["total_amount"] == "280.00"  # revenue is still known

    def test_one_unknown_cost_makes_the_whole_sale_profit_unavailable(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["sugar"], "5"), item(shelf["oil"], "1")])

        assert sale["items"][0]["profit"] == "123.35" and sale["items"][1]["profit"] is None
        assert sale["gross_profit"] is None and sale["cogs_total"] is None and sale["lines_without_cost"] == 1

    def test_profit_uses_the_same_net_amount_as_revenue_after_discounts(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["sugar"], "5", discount="40")], header={"discount": "10"})

        # line net 200; cogs 116.65; bill discount 10 -> total 190; profit 190 - 116.65 = 73.35
        assert sale["items"][0]["line_total"] == "200.00" and sale["items"][0]["profit"] == "83.35"
        assert (sale["total_amount"], sale["gross_profit"]) == ("190.00", "73.35")

    def test_a_loss_is_a_negative_profit(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "1", unit_price="15")])
        assert sale["gross_profit"] == "-5.00"

    def test_a_drafts_cost_is_hidden_until_posting(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["sugar"], "5")])
        assert sale["items"][0]["unit_cost"] is None and sale["gross_profit"] is None


class TestPayment:
    def test_paid_in_full_by_default_needs_a_method(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2")])

        no_method = post_bare(client_a, sale)
        assert no_method.status_code == 422 and "payment_method" in errors(no_method)

        ok = post(client_a, sale, payment_method="CASH")
        assert ok.status_code == 200
        data = ok.json()
        assert (data["payment_type"], data["amount_paid"], data["payment_method"]) == (
            "PAID",
            "100.00",
            "CASH",
        )
        assert data["credit_amount"] == "0.00"

    def test_upi_with_a_reference(self, client_a, shelf):
        data = sold(client_a, [item(shelf["rice"])], payment_method="UPI", payment_reference=" UPI-778 ")
        assert (data["payment_method"], data["payment_reference"]) == ("UPI", "UPI-778")

    def test_a_fully_paid_sale_puts_nothing_on_the_khata(self, client_a, shelf, fresh):
        c = make_customer(client_a)
        sold(client_a, [item(shelf["rice"], "2")], header={"customer_id": c["id"]}, payment_method="CASH")
        assert owed(client_a, c) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_a_cash_sale_needs_no_customer(self, client_a, shelf):
        assert sold(client_a, [item(shelf["rice"])], payment_method="CASH")["customer_id"] is None

    def test_a_part_payment_is_a_credit_sale_and_charges_the_khata(self, client_a, shelf):
        c = make_customer(client_a)
        # total 1000 (20 x 50), paid 700 -> 300 owed
        sale = sold(
            client_a, [item(shelf["rice"], "10", unit_price="100")], header={"customer_id": c["id"]},
            amount_paid="700", payment_method="CASH",
        )  # fmt: skip

        assert (sale["payment_type"], sale["amount_paid"], sale["credit_amount"]) == (
            "CREDIT",
            "700.00",
            "300.00",
        )
        assert owed(client_a, c) == Decimal("300.00")
        ledger = client_a.get(f"{CUSTOMERS}/{c['id']}/ledger").json()["items"][0]
        assert (ledger["entry_type"], ledger["amount_delta"]) == ("CREDIT_SALE", "300.00")
        assert (ledger["reference_type"], ledger["reference_id"], ledger["reference_no"]) == (
            "SALE",
            sale["id"],
            sale["invoice_no"],
        )

    def test_a_sale_fully_on_credit_needs_no_payment_method(self, client_a, shelf):
        c = make_customer(client_a)
        sale = sold(client_a, [item(shelf["rice"], "2")], header={"customer_id": c["id"]}, amount_paid="0")
        assert (sale["payment_type"], sale["payment_method"], sale["credit_amount"]) == (
            "CREDIT",
            None,
            "100.00",
        )
        assert owed(client_a, c) == Decimal("100.00")

    def test_credit_needs_a_customer(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2")])
        response = post(client_a, sale, amount_paid="20", payment_method="CASH")
        assert response.status_code == 422 and "customer_id" in errors(response)
        assert stock(client_a, shelf["rice"]) == 10

    def test_paying_more_than_the_total_is_refused_not_silently_lost(self, client_a, shelf):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"], "2")], customer_id=c["id"])
        response = post(client_a, sale, amount_paid="150", payment_method="CASH")
        assert response.status_code == 422 and "advance" in errors(response)["amount_paid"]
        assert stock(client_a, shelf["rice"]) == 10 and owed(client_a, c) == 0

    def test_an_advance_on_the_khata_is_used_up_by_a_credit_sale(self, client_a, shelf):
        c = make_customer(client_a)
        client_a.post(f"{CUSTOMERS}/{c['id']}/payments", json={"amount": "500"})  # advance
        sold(client_a, [item(shelf["rice"], "4")], header={"customer_id": c["id"]}, amount_paid="0")
        assert owed(client_a, c) == Decimal("-300.00")

    def test_bad_payments_are_refused(self, client_a, shelf):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"], "2")], customer_id=c["id"])
        for payment in (
            {"amount_paid": "-1"},
            {"amount_paid": "1.005"},
            {"payment_method": "BITCOIN"},
            {"amount_paid": 5.5},
        ):
            assert client_a.post(f"{API}/{sale['id']}/post", json=payment).status_code == 422

    def test_an_inactive_customer_cannot_be_given_credit_and_nothing_is_written(self, client_a, shelf, fresh):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"], "2")], customer_id=c["id"])
        client_a.post(f"{CUSTOMERS}/{c['id']}/deactivate")

        response = post(client_a, sale, amount_paid="0")

        assert response.status_code == 409 and "inactive" in response.text
        assert stock(client_a, shelf["rice"]) == 10
        assert client_a.get(f"{API}/{sale['id']}").json()["status"] == "DRAFT"
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_an_inactive_customer_can_still_buy_for_cash(self, client_a, shelf):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"])], customer_id=c["id"])
        client_a.post(f"{CUSTOMERS}/{c['id']}/deactivate")
        assert post(client_a, sale, payment_method="CASH").status_code == 200

    def test_a_zero_total_bill_cannot_be_posted(self, client_a, shelf):
        response = post(
            client_a, draft(client_a, [item(shelf["rice"], "1", discount="50")]), payment_method="CASH"
        )
        assert response.status_code == 422


class TestDoublePostingAndImmutability:
    def test_a_sale_cannot_be_posted_twice(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "3")])
        assert post(client_a, sale, payment_method="CASH").status_code == 200

        again = post(client_a, sale, payment_method="CASH")

        assert again.status_code == 409 and "already been posted" in again.json()["detail"][0]["msg"]
        assert stock(client_a, shelf["rice"]) == 7  # not 4

    def test_a_posted_sale_cannot_be_edited(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        url = f"{API}/{sale['id']}"

        attempts = [
            client_a.patch(url, json={"notes": "x"}),
            client_a.put(f"{url}/items", json={"items": [item(shelf["rice"], "9")]}),
        ]

        assert [a.status_code for a in attempts] == [409, 409]
        assert client_a.get(url).json()["items"][0]["quantity"] == "1.000"

    def test_posting_an_inactive_product_is_refused_and_changes_nothing(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"], "2"), item(shelf["sugar"], "1")])
        client_a.post(f"{PRODUCTS}/{shelf['sugar']['id']}/deactivate")

        response = post(client_a, sale, payment_method="CASH")

        assert response.status_code == 422 and "items.1.product_id" in errors(response)
        assert stock(client_a, shelf["rice"]) == 10

    def test_posting_is_audited_with_before_and_after(self, client_a, shelf, fresh):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        log = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "sale", AuditLog.action == "post")
            ).one()
        )
        assert (
            log.entity_id == sale["id"]
            and log.before_json["status"] == "DRAFT"
            and log.after_json["status"] == "POSTED"
        )
        assert (
            log.after_json["invoice_no"] == sale["invoice_no"] and log.after_json["total_amount"] == "100.00"
        )

    def test_detail_shows_the_inventory_effect(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        effects = client_a.get(f"{API}/{sale['id']}").json()["items"][0]["inventory_effects"]
        assert [(e["txn_type"], e["qty_delta"]) for e in effects] == [("SALE", "-2.000")]


class TestVoid:
    def test_voiding_a_posted_sale_puts_the_stock_back_and_keeps_the_record(self, client_a, shelf, fresh):
        sale = sold(client_a, [item(shelf["rice"], "4")], payment_method="CASH")

        response = client_a.post(f"{API}/{sale['id']}/void", json={"reason": "Wrong bill"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "VOID" and data["void_reason"] == "Wrong bill" and data["voided_at"]
        assert data["invoice_no"] == sale["invoice_no"]  # the number is never reused
        assert stock(client_a, shelf["rice"]) == 10
        assert [(e["txn_type"], e["qty_delta"]) for e in data["items"][0]["inventory_effects"]] == [
            ("SALE", "-4.000"),
            ("REVERSAL", "4.000"),
        ]
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(SaleItem))) == 1  # nothing deleted

    def test_voiding_a_credit_sale_takes_the_credit_off_the_khata(self, client_a, shelf):
        c = make_customer(client_a)
        sale = sold(
            client_a,
            [item(shelf["rice"], "2")],
            header={"customer_id": c["id"]},
            amount_paid="40",
            payment_method="CASH",
        )
        assert owed(client_a, c) == Decimal("60.00")

        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "Wrong customer"})

        assert owed(client_a, c) == Decimal("0.00")
        entries = client_a.get(f"{CUSTOMERS}/{c['id']}/ledger").json()["items"]
        assert [e["entry_type"] for e in entries] == ["REVERSAL", "CREDIT_SALE"]  # both stay in the history
        assert entries[1]["reversed_by_entry_id"] == entries[0]["id"]

    def test_voiding_a_fully_paid_sale_touches_no_khata(self, client_a, shelf, fresh):
        c = make_customer(client_a)
        sale = sold(client_a, [item(shelf["rice"])], header={"customer_id": c["id"]}, payment_method="CASH")
        assert client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"}).status_code == 200
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_a_reason_is_required(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        url = f"{API}/{sale['id']}/void"
        assert client_a.post(url, json={}).status_code == 422
        assert client_a.post(url, json={"reason": "  "}).status_code == 422
        assert client_a.post(url, json={"reason": "x" * 501}).status_code == 422
        assert stock(client_a, shelf["rice"]) == 9

    def test_a_void_sale_cannot_be_voided_or_posted_again(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})

        assert client_a.post(f"{API}/{sale['id']}/void", json={"reason": "again"}).status_code == 409
        assert post(client_a, sale, payment_method="CASH").status_code == 409
        assert stock(client_a, shelf["rice"]) == 10  # only one reversal

    def test_voiding_a_draft_discards_it(self, client_a, shelf, fresh):
        sale = draft(client_a, [item(shelf["rice"])])
        response = client_a.post(f"{API}/{sale['id']}/void", json={"reason": "Not needed"})
        assert response.status_code == 200 and response.json()["invoice_no"] is None
        assert sold(client_a, [item(shelf["rice"])], payment_method="CASH")["invoice_no"].endswith("/0001")

    def test_a_sale_with_a_live_return_cannot_be_voided(self, client_a, tenant_a, shelf, session_factory):
        from app.models import SalesReturn
        from app.models.enums import DocumentStatus, RefundMode

        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        with session_factory() as s, s.begin():  # a return document, as Phase 9 will create it
            s.add(
                SalesReturn(
                    shop_id=tenant_a.shop.id, sale_id=sale["id"], return_date=today_in_shop_timezone(),
                    refund_mode=RefundMode.CASH, total_refund=Decimal("50"), status=DocumentStatus.POSTED,
                    created_by=tenant_a.user.id,
                )
            )  # fmt: skip

        response = client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})

        assert response.status_code == 409 and "returns" in response.text
        assert stock(client_a, shelf["rice"]) == 8

    def test_voiding_is_audited(self, client_a, shelf, fresh):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "Wrong bill"})
        log = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.action == "void", AuditLog.entity_type == "sale")
            ).one()
        )
        assert log.before_json["status"] == "POSTED" and log.after_json["void_reason"] == "Wrong bill"

    def test_a_voided_sale_frees_stock_that_can_be_sold_again(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "10")], payment_method="CASH")
        assert stock(client_a, shelf["rice"]) == 0
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})
        assert sold(client_a, [item(shelf["rice"], "10")], payment_method="CASH")["status"] == "POSTED"


class TestCorrect:
    def test_a_voided_sale_can_be_copied_into_a_new_draft_and_reposted(self, client_a, shelf):
        c = make_customer(client_a)
        original = sold(
            client_a, [item(shelf["rice"], "3", discount="10")], header={"customer_id": c["id"], "notes": "First try", "discount": "5"},
            payment_method="CASH",
        )  # fmt: skip
        client_a.post(f"{API}/{original['id']}/void", json={"reason": "Wrong quantity"})

        copy = client_a.post(f"{API}/{original['id']}/correct")

        assert copy.status_code == 201
        data = copy.json()
        assert (
            data["status"] == "DRAFT" and data["invoice_no"] is None and data["replaces_id"] == original["id"]
        )
        assert (data["customer_id"], data["notes"], data["discount"]) == (c["id"], "First try", "5.00")
        assert [(i["quantity"], i["unit_price"], i["discount"]) for i in data["items"]] == [
            ("3.000", "50.00", "10.00")
        ]
        assert client_a.get(f"{API}/{original['id']}").json()["replaced_by_id"] == data["id"]

        client_a.put(f"{API}/{data['id']}/items", json={"items": [item(shelf["rice"], "2")]})
        reposted = post(client_a, data, payment_method="CASH")
        assert reposted.status_code == 200 and stock(client_a, shelf["rice"]) == 8

    def test_only_a_voided_sale_can_be_corrected_and_only_once(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        assert client_a.post(f"{API}/{sale['id']}/correct").status_code == 409  # still posted
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{API}/{sale['id']}/correct").status_code == 201
        assert client_a.post(f"{API}/{sale['id']}/correct").status_code == 409

    def test_a_discarded_draft_cannot_be_corrected(self, client_a, shelf):
        sale = draft(client_a, [item(shelf["rice"])])
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{API}/{sale['id']}/correct").status_code == 409


class TestListAndSearch:
    @pytest.fixture
    def three(self, client_a, shelf):
        asha = make_customer(client_a, "Asha Devi", phone="9000000001")
        a = sold(
            client_a,
            [item(shelf["rice"], "1")],
            header={"customer_id": asha["id"], "sale_date": "2026-01-10"},
            payment_method="CASH",
        )
        b = sold(
            client_a,
            [item(shelf["rice"], "2")],
            header={"customer_id": asha["id"], "sale_date": "2026-02-10", "notes": "festival"},
            amount_paid="0",
        )
        c = draft(client_a, [item(shelf["sugar"], "1")], sale_date="2026-03-10")
        return asha, a, b, c

    def ids(self, client, **params):
        response = client.get(API, params=params)
        assert response.status_code == 200, response.text
        return [s["id"] for s in response.json()["items"]]

    def test_newest_first_with_totals_and_counts(self, client_a, three):
        _, a, b, c = three
        data = client_a.get(API).json()
        assert [s["id"] for s in data["items"]] == [c["id"], b["id"], a["id"]] and data["total"] == 3
        row = data["items"][1]
        assert (row["total_amount"], row["amount_paid"], row["payment_type"], row["item_count"]) == (
            "100.00",
            "0.00",
            "CREDIT",
            1,
        )
        assert row["customer_name"] == "Asha Devi" and "items" not in row

    def test_search_by_invoice_customer_phone_or_notes(self, client_a, three):
        asha, a, b, c = three
        assert self.ids(client_a, q=a["invoice_no"]) == [a["id"]]
        assert self.ids(client_a, q="asha") == [b["id"], a["id"]]
        assert self.ids(client_a, q="90000 00001") == [b["id"], a["id"]]
        assert self.ids(client_a, q="festival") == [b["id"]]
        assert self.ids(client_a, q="%") == [] and self.ids(client_a, q="nothing") == []

    def test_filters(self, client_a, three):
        asha, a, b, c = three
        assert self.ids(client_a, status="DRAFT") == [c["id"]]
        assert self.ids(client_a, status=["DRAFT", "POSTED"]) == [c["id"], b["id"], a["id"]]
        assert self.ids(client_a, payment_type="CREDIT") == [b["id"]]
        assert self.ids(client_a, customer_id=asha["id"]) == [b["id"], a["id"]]
        assert self.ids(client_a, date_from="2026-02-01") == [c["id"], b["id"]]
        assert self.ids(client_a, date_to="2026-01-31") == [a["id"]]
        assert client_a.get(API, params={"status": "BOGUS"}).status_code == 422
        assert (
            client_a.get(API, params={"date_from": "2026-05-01", "date_to": "2026-01-01"}).status_code == 422
        )

    def test_pagination(self, client_a, three):
        _, a, b, c = three
        page = client_a.get(API, params={"limit": 2, "offset": 1}).json()
        assert [s["id"] for s in page["items"]] == [b["id"], a["id"]] and page["total"] == 3
        assert client_a.get(API, params={"limit": 201}).status_code == 422


class TestTenantIsolation:
    def test_another_shops_sale_is_not_found_by_any_endpoint(self, client_a, client_b, shelf):
        sale = sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        url = f"{API}/{sale['id']}"

        responses = [
            client_b.get(url),
            client_b.patch(url, json={"notes": "x"}),
            client_b.put(f"{url}/items", json={"items": []}),
            client_b.post(f"{url}/post"),
            client_b.post(f"{url}/void", json={"reason": "x"}),
            client_b.post(f"{url}/correct"),
        ]

        assert [r.status_code for r in responses] == [404] * 6
        assert client_a.get(url).json()["status"] == "POSTED" and stock(client_a, shelf["rice"]) == 9

    def test_lists_never_show_another_shops_sales(self, client_a, client_b, shelf):
        sold(client_a, [item(shelf["rice"])], payment_method="CASH")
        assert client_b.get(API).json()["total"] == 0

    def test_shops_have_independent_numbering_and_stock(self, client_a, client_b, tenant_b, units, shelf):
        supplier = make_supplier(client_b, "Theirs")
        theirs = make_product(client_b, tenant_b, units, "RICE", selling_price="50")
        stock_up(client_b, supplier, theirs, 5, 10)

        mine = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        other = sold(client_b, [item(theirs, "1")], payment_method="CASH")

        assert mine["invoice_no"] == other["invoice_no"]  # both 0001 of their own shop
        assert stock(client_a, shelf["rice"]) == 8 and stock(client_b, theirs) == 4

    def test_a_sale_cannot_use_another_shops_customer_or_product_even_in_the_database(
        self, session, tenant_a, tenant_b
    ):
        from app.models.enums import SaleStatus
        from tests import factories
        from tests.conftest import assert_rejected

        foreign_customer = factories.make_customer(session, tenant_b.shop)
        session.commit()
        sale = Sale(
            shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=today_in_shop_timezone(),
            customer_id=foreign_customer.id, created_by=tenant_a.user.id,
        )  # fmt: skip
        assert_rejected(session, sale, match="FOREIGN KEY")


class TestEveryBusinessType:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_a_sale_works_the_same_for_every_business_type(
        self, make_client, tenant_of, units, business_type
    ):
        tenant = tenant_of(business_type)
        client = make_client(tenant)
        supplier = make_supplier(client)
        product = make_product(client, tenant, units, "ITEM", unit="kg", selling_price="48")
        stock_up(client, supplier, product, 100, 20)
        stock_up(client, supplier, product, 50, 30)
        buyer = make_customer(client, "Buyer")

        sale = sold(
            client,
            [item(product, "5")],
            header={"customer_id": buyer["id"]},
            amount_paid="100",
            payment_method="UPI",
        )

        assert (
            sale["status"] == "POSTED" and sale["cogs_total"] == "116.65" and sale["gross_profit"] == "123.35"
        )
        assert stock(client, product) == 145 and owed(client, buyer) == Decimal("140.00")


class TestStaff:
    def test_a_staff_user_can_bill(self, make_client, tenant_a, shelf):
        from app.models.enums import UserRole

        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert sold(staff, [item(shelf["rice"])], payment_method="CASH")["status"] == "POSTED"


class TestCalculateThePayment:
    def test_it_previews_the_paid_and_credit_split(self, client_a, shelf):
        base = {"items": [item(shelf["rice"], "10", unit_price="100")]}

        full = client_a.post(f"{API}/calculate", json=base).json()
        part = client_a.post(f"{API}/calculate", json={**base, "amount_paid": "700"}).json()
        none = client_a.post(f"{API}/calculate", json={**base, "amount_paid": "0"}).json()

        assert (full["payment_type"], full["paid"], full["credit"]) == ("PAID", "1000.00", "0.00")
        assert (part["payment_type"], part["paid"], part["credit"]) == ("CREDIT", "700.00", "300.00")
        assert (none["paid"], none["credit"]) == ("0.00", "1000.00")

    def test_it_flags_an_overpayment_without_failing(self, client_a, shelf):
        data = client_a.post(
            f"{API}/calculate", json={"items": [item(shelf["rice"], "2")], "amount_paid": "150"}
        ).json()
        assert data["errors"][0]["field"] == "amount_paid" and data["credit"] == "0.00"


class TestShopUpi:
    def test_the_shops_upi_id_is_shown_for_the_billing_screen(self, client_a, tenant_a, session_factory):
        from sqlalchemy import update

        from app.models import Shop

        assert client_a.get("/api/v1/shop").json()["upi_id"] is None
        with session_factory() as s, s.begin():
            s.execute(update(Shop).where(Shop.id == tenant_a.shop.id).values(upi_id="mystore@upi"))
        assert client_a.get("/api/v1/shop").json()["upi_id"] == "mystore@upi"
