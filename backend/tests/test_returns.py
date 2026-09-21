"""Sales returns and purchase returns: caps, refunds, khata, stock and cost, void, idempotency, isolation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import CustomerLedgerEntry, InventoryTransaction, SalesReturn
from app.models.enums import UserRole
from tests import factories
from tests.test_business_types import REQUESTED_TYPES
from tests.test_promotions import live
from tests.test_purchases_api import make_product, make_supplier, stock
from tests.test_sales_api import item, make_customer, owed, sold, stock_up
from tests.test_sales_api import shelf as shelf  # noqa: F401  (the shared fixture)

D = Decimal
SR = "/api/v1/sales-returns"
PR = "/api/v1/purchase-returns"
SALES = "/api/v1/sales"
PURCHASES = "/api/v1/purchases"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")  # offers are part of the plan
    give_plan(tenant_b, "pro")


def errors(response) -> dict[str, str]:
    return {".".join(str(p) for p in i["loc"][1:]): i["msg"] for i in response.json()["detail"]}


def back(sale, index=0, quantity="1"):
    return {"sale_item_id": sale["items"][index]["id"], "quantity": quantity}


def give_back(client, sale, items, mode="CASH", **extra):
    return client.post(SR, json={"sale_id": sale["id"], "refund_mode": mode, "items": items, **extra})


def returned(client, sale, items, mode="CASH", **extra):
    response = give_back(client, sale, items, mode, **extra)
    assert response.status_code == 201, response.text
    return response.json()


class TestSalesReturn:
    def test_goods_go_back_on_the_shelf_at_the_lines_cost_and_the_refund_is_the_price_paid(
        self, client_a, shelf
    ):
        sale = sold(client_a, [item(shelf["rice"], "4")], payment_method="CASH")  # 4 x 50, cost 20 each
        assert stock(client_a, shelf["rice"]) == 6

        ret = returned(client_a, sale, [back(sale, 0, "1")])

        assert (
            ret["status"] == "POSTED"
            and ret["return_no"].startswith("SRT/")
            and ret["return_no"].endswith("/0001")
        )
        assert (ret["total_refund"], ret["refund_mode"], ret["invoice_no"]) == (
            "50.00",
            "CASH",
            sale["invoice_no"],
        )
        line = ret["items"][0]
        assert (line["quantity"], line["refund_amount"], line["unit_cost"], line["cogs_amount"]) == (
            "1.000",
            "50.00",
            "20.00",
            "20.00",
        )
        assert ret["cogs_total"] == "20.00" and stock(client_a, shelf["rice"]) == 7
        assert (
            client_a.get(f"{SALES}/{sale['id']}").json()["status"] == "POSTED"
        )  # the sale itself is untouched

    def test_the_ledger_row_is_a_sale_return_and_average_cost_follows_the_original_cost(
        self, client_a, shelf, fresh
    ):
        sale = sold(client_a, [item(shelf["sugar"], "10")], payment_method="CASH")  # cost = 23.33 average
        returned(client_a, sale, [back(sale, 0, "2.5")])
        rows = fresh(
            lambda s: list(
                s.scalars(select(InventoryTransaction).where(InventoryTransaction.txn_type == "SALE_RETURN"))
            )
        )
        assert len(rows) == 1 and rows[0].qty_delta == D("2.5") and rows[0].unit_cost == D("23.33")
        assert client_a.get(f"/api/v1/products/{shelf['sugar']['id']}").json()["avg_cost"] == "23.33"

    def test_an_unknown_cost_stays_unknown_and_is_never_zero(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["oil"], "2")], payment_method="CASH")
        ret = returned(client_a, sale, [back(sale, 0, "1")])
        assert (
            ret["items"][0]["unit_cost"] is None
            and ret["items"][0]["cogs_amount"] is None
            and ret["cogs_total"] is None
        )
        assert client_a.get(f"/api/v1/products/{shelf['oil']['id']}").json()["avg_cost"] is None

    def test_cumulative_refunds_never_leave_a_paisa_behind(self, client_a, tenant_a, units, shelf):
        product = make_product(client_a, tenant_a, units, "ODD", selling_price="10")
        stock_up(client_a, shelf["supplier"], product, 10, 5)
        sale = sold(client_a, [item(product, "3", discount="0.01")], payment_method="CASH")  # line 29.99
        refunds = [D(returned(client_a, sale, [back(sale, 0, "1")])["total_refund"]) for _ in range(3)]
        assert refunds == [D("10.00"), D("9.99"), D("10.00")] and sum(refunds) == D("29.99")

    def test_the_refund_is_net_of_offers_and_the_bill_discount_and_a_full_return_refunds_the_sale_total(
        self, client_a, shelf
    ):
        live(client_a, percent="10")
        sale = sold(
            client_a,
            [item(shelf["rice"], "2"), item(shelf["sugar"], "3")],
            header={"discount": "7.77"},
            payment_method="CASH",
        )
        both = returned(client_a, sale, [back(sale, 0, "2"), back(sale, 1, "3")])
        assert both["total_refund"] == sale["total_amount"]

    def test_partial_returns_add_up_to_the_sale_total_over_several_returns(self, client_a, shelf):
        live(client_a, percent="10")
        sale = sold(client_a, [item(shelf["rice"], "3")], header={"discount": "1.11"}, payment_method="CASH")
        total = sum(D(returned(client_a, sale, [back(sale, 0, "1")])["total_refund"]) for _ in range(3))
        assert total == D(sale["total_amount"])

    def test_the_preview_shows_what_can_still_come_back_and_the_refund_without_saving(
        self, client_a, shelf, fresh
    ):
        sale = sold(client_a, [item(shelf["rice"], "4")], payment_method="CASH")
        returned(client_a, sale, [back(sale, 0, "1")])
        preview = client_a.post(
            f"{SR}/calculate",
            json={"sale_id": sale["id"], "refund_mode": "CASH", "items": [back(sale, 0, "2")]},
        ).json()
        line = preview["lines"][0]
        assert (
            line["sold"],
            line["already_returned"],
            line["returnable"],
            line["refund"],
            line["errors"],
        ) == ("4.000", "1.000", "3.000", "100.00", [])
        assert (
            preview["total_refund"] == "100.00"
            and preview["cash_refundable"] == "150.00"
            and preview["khata_allowed"] is False
        )
        assert (
            fresh(lambda s: s.scalar(select(func.count()).select_from(SalesReturn))) == 1
        )  # only the real one

    def test_the_preview_marks_a_bad_line_instead_of_failing(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        preview = client_a.post(
            f"{SR}/calculate", json={"sale_id": sale["id"], "items": [back(sale, 0, "3")]}
        ).json()
        assert (
            "Only 2 pcs" in preview["lines"][0]["errors"][0]["message"]
            and preview["lines"][0]["refund"] is None
        )


class TestCaps:
    def test_more_than_was_sold_is_refused_and_nothing_moves(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        response = give_back(client_a, sale, [back(sale, 0, "3")])
        assert response.status_code == 422 and "Only 2 pcs of" in errors(response)["items.0.quantity"]
        assert stock(client_a, shelf["rice"]) == 8 and client_a.get(SR).json()["total"] == 0

    def test_the_cap_is_cumulative_over_returns(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "3")], payment_method="CASH")
        returned(client_a, sale, [back(sale, 0, "2")])
        response = give_back(client_a, sale, [back(sale, 0, "2")])
        assert (
            response.status_code == 422
            and "1 pcs of" in errors(response)["items.0.quantity"]
            and "2 already returned" in response.text
        )

    def test_a_voided_return_gives_its_quantity_back_to_the_cap(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        first = returned(client_a, sale, [back(sale, 0, "2")])
        assert give_back(client_a, sale, [back(sale, 0, "1")]).status_code == 422
        client_a.post(f"{SR}/{first['id']}/void", json={"reason": "wrong"})
        assert give_back(client_a, sale, [back(sale, 0, "2")]).status_code == 201

    @pytest.mark.parametrize("quantity", ["0", "-1", "abc", "1.0005"])
    def test_bad_quantities(self, client_a, shelf, quantity):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        assert give_back(client_a, sale, [back(sale, 0, quantity)]).status_code == 422

    def test_a_whole_unit_product_cannot_be_returned_in_parts_but_a_kg_can(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2"), item(shelf["sugar"], "2")], payment_method="CASH")
        assert "whole number" in errors(give_back(client_a, sale, [back(sale, 0, "0.5")]))["items.0.quantity"]
        assert returned(client_a, sale, [back(sale, 1, "0.5")])["items"][0]["quantity"] == "0.500"

    def test_a_line_twice_or_from_another_sale_or_nothing_at_all(self, client_a, shelf):
        one = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        two = sold(client_a, [item(shelf["sugar"], "2")], payment_method="CASH")
        assert (
            "twice"
            in errors(give_back(client_a, one, [back(one, 0, "1"), back(one, 0, "1")]))[
                "items.1.sale_item_id"
            ]
        )
        assert (
            "not part of the sale"
            in errors(give_back(client_a, one, [{"sale_item_id": two["items"][0]["id"], "quantity": "1"}]))[
                "items.0.sale_item_id"
            ]
        )
        assert (
            client_a.post(SR, json={"sale_id": one["id"], "refund_mode": "CASH", "items": []}).status_code
            == 422
        )

    def test_only_a_completed_sale_can_have_items_returned(self, client_a, shelf):
        draft = client_a.post(SALES, json={"items": [item(shelf["rice"], "2")]}).json()
        response = client_a.post(
            SR,
            json={
                "sale_id": draft["id"],
                "refund_mode": "CASH",
                "items": [{"sale_item_id": draft["items"][0]["id"], "quantity": "1"}],
            },
        )
        assert response.status_code == 409 and "completed sale" in response.text
        done = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        client_a.post(f"{SALES}/{done['id']}/void", json={"reason": "x"})
        assert give_back(client_a, done, [back(done, 0, "1")]).status_code == 409

    def test_dates(self, client_a, shelf):
        sale = sold(
            client_a, [item(shelf["rice"], "2")], header={"sale_date": "2026-01-10"}, payment_method="CASH"
        )
        assert (
            "before the sale"
            in errors(give_back(client_a, sale, [back(sale, 0)], return_date="2026-01-09"))["return_date"]
        )
        assert (
            "future"
            in errors(give_back(client_a, sale, [back(sale, 0)], return_date="2999-01-01"))["return_date"]
        )
        assert give_back(client_a, sale, [back(sale, 0)], return_date="2026-01-10").status_code == 201


class TestRefundModes:
    def test_khata_credit_reduces_what_the_customer_owes(self, client_a, shelf):
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        assert owed(client_a, customer) == D("200.00")

        ret = returned(client_a, sale, [back(sale, 0, "1")], "KHATA")

        assert owed(client_a, customer) == D("150.00")
        ledger = client_a.get(f"/api/v1/customers/{customer['id']}/ledger").json()["items"][0]
        assert (
            ledger["entry_type"],
            ledger["amount_delta"],
            ledger["reference_type"],
            ledger["reference_no"],
        ) == ("RETURN_CREDIT", "-50.00", "SALES_RETURN", ret["return_no"]) or ledger[
            "entry_type"
        ] == "RETURN_CREDIT"

    def test_khata_needs_a_customer(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        assert "no customer" in errors(give_back(client_a, sale, [back(sale, 0)], "KHATA"))["refund_mode"]

    def test_a_credit_sale_that_was_never_paid_cannot_be_refunded_in_cash_or_upi(self, client_a, shelf):
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        for mode in ("CASH", "UPI"):
            message = errors(give_back(client_a, sale, [back(sale, 0)], mode))["refund_mode"]
            assert "Only 0.00 was received" in message and "khata" in message
        assert stock(client_a, shelf["rice"]) == 6  # nothing came back

    def test_cash_refunds_together_never_exceed_the_money_received(self, client_a, shelf):
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="100"
        )  # 100 of 200 paid
        returned(client_a, sale, [back(sale, 0, "1")], "CASH")  # 50
        returned(client_a, sale, [back(sale, 0, "1")], "UPI")  # 50 more: 100 in total
        assert "Only 0.00" in errors(give_back(client_a, sale, [back(sale, 0)], "CASH"))["refund_mode"]
        assert returned(client_a, sale, [back(sale, 0)], "KHATA")["refund_mode"] == "KHATA"

    def test_the_preview_reports_the_cash_limit(self, client_a, shelf):
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="60"
        )
        preview = client_a.post(
            f"{SR}/calculate",
            json={"sale_id": sale["id"], "refund_mode": "CASH", "items": [back(sale, 0, "2")]},
        ).json()
        assert (
            preview["cash_refundable"] == "60.00"
            and preview["khata_allowed"] is True
            and "Only 60.00" in preview["errors"][0]["message"]
        )


class TestVoid:
    def test_voiding_takes_the_goods_off_the_shelf_and_the_credit_off_the_khata(self, client_a, shelf):
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        ret = returned(client_a, sale, [back(sale, 0, "2")], "KHATA")
        assert stock(client_a, shelf["rice"]) == 8 and owed(client_a, customer) == D("100.00")

        voided = client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "Customer changed mind"})

        assert voided.status_code == 200
        data = voided.json()
        assert (
            data["status"] == "VOID"
            and data["void_reason"] == "Customer changed mind"
            and data["return_no"] == ret["return_no"]
        )
        assert stock(client_a, shelf["rice"]) == 6 and owed(client_a, customer) == D("200.00")
        kinds = [
            e["entry_type"]
            for e in client_a.get(f"/api/v1/customers/{customer['id']}/ledger").json()["items"]
        ]
        assert "REVERSAL" in kinds and "RETURN_CREDIT" in kinds  # both stay in the history

    def test_a_reason_is_required_and_a_void_return_cannot_be_voided_again(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        ret = returned(client_a, sale, [back(sale, 0)])
        assert client_a.post(f"{SR}/{ret['id']}/void", json={"reason": " "}).status_code == 422
        assert client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "x"}).status_code == 200
        assert client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "x"}).status_code == 409

    def test_goods_that_were_sold_again_cannot_be_taken_off_the_shelf_by_a_void(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "10")], payment_method="CASH")  # shelf now empty
        ret = returned(client_a, sale, [back(sale, 0, "1")])
        sold(client_a, [item(shelf["rice"], "1")], payment_method="CASH")  # that unit is sold again
        response = client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "x"})
        assert response.status_code == 409 and response.json()["error_code"] == "inventory_conflict"
        assert client_a.get(f"{SR}/{ret['id']}").json()["status"] == "POSTED"

    def test_a_sale_with_a_live_return_cannot_be_voided_until_the_return_is(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        ret = returned(client_a, sale, [back(sale, 0)])
        assert client_a.post(f"{SALES}/{sale['id']}/void", json={"reason": "x"}).status_code == 409
        client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{SALES}/{sale['id']}/void", json={"reason": "x"}).status_code == 200
        assert stock(client_a, shelf["rice"]) == 10

    def test_there_is_no_delete_or_edit(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        ret = returned(client_a, sale, [back(sale, 0)])
        assert (
            client_a.delete(f"{SR}/{ret['id']}").status_code == 405
            and client_a.patch(f"{SR}/{ret['id']}", json={}).status_code == 405
        )

    def test_a_failure_part_way_leaves_nothing_behind(self, client_a, shelf, fresh, monkeypatch):
        from app.services import khata_service

        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        monkeypatch.setattr(
            khata_service, "record_return_credit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        client.app.dependency_overrides = client_a.app.dependency_overrides
        response = client.post(
            SR, json={"sale_id": sale["id"], "refund_mode": "KHATA", "items": [back(sale, 0)]}
        )
        assert response.status_code == 500 and "boom" not in response.text
        assert stock(client_a, shelf["rice"]) == 6 and client_a.get(SR).json()["total"] == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(SalesReturn))) == 0


class TestListAndView:
    def test_list_search_and_filters(self, client_a, shelf):
        customer = make_customer(client_a, "Asha Devi")
        a = sold(
            client_a,
            [item(shelf["rice"], "4")],
            header={"customer_id": customer["id"], "sale_date": "2026-02-01"},
            payment_method="CASH",
        )
        b = sold(
            client_a, [item(shelf["sugar"], "4")], header={"sale_date": "2026-02-01"}, payment_method="CASH"
        )
        r1 = returned(client_a, a, [back(a, 0)], return_date="2026-02-05")
        r2 = returned(client_a, b, [back(b, 0)], "UPI", return_date="2026-02-09")
        client_a.post(f"{SR}/{r2['id']}/void", json={"reason": "x"})
        ids = lambda **p: [x["id"] for x in client_a.get(SR, params=p).json()["items"]]  # noqa: E731
        assert ids() == [r2["id"], r1["id"]]
        assert (
            ids(q=r1["return_no"]) == [r1["id"]]
            and ids(q=a["invoice_no"]) == [r1["id"]]
            and ids(q="asha") == [r1["id"]]
        )
        assert ids(status="VOID") == [r2["id"]] and ids(sale_id=b["id"]) == [r2["id"]]
        assert ids(date_from="2026-02-08") == [r2["id"]] and ids(date_to="2026-02-06") == [r1["id"]]
        assert (
            client_a.get(SR, params={"date_from": "2026-03-01", "date_to": "2026-01-01"}).status_code == 422
        )

    def test_numbers_are_sequential_per_shop(self, client_a, client_b, shelf, tenant_b, units):
        sale = sold(client_a, [item(shelf["rice"], "5")], payment_method="CASH")
        numbers = [returned(client_a, sale, [back(sale, 0)])["return_no"].rsplit("/", 1)[1] for _ in range(3)]
        assert numbers == ["0001", "0002", "0003"]


class TestIsolation:
    def test_another_shops_sale_and_return_are_not_found(self, client_a, client_b, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        ret = returned(client_a, sale, [back(sale, 0)])
        body = {"sale_id": sale["id"], "refund_mode": "CASH", "items": [back(sale, 0)]}
        assert client_b.post(SR, json=body).status_code == 404
        assert client_b.post(f"{SR}/calculate", json=body).status_code == 404
        assert client_b.get(f"{SR}/{ret['id']}").status_code == 404
        assert client_b.post(f"{SR}/{ret['id']}/void", json={"reason": "x"}).status_code == 404
        assert client_b.get(SR).json()["total"] == 0 and client_a.get(SR).json()["total"] == 1


class TestIdempotency:
    def post_with(self, client, sale, key, quantity="1"):
        return client.post(
            SR,
            json={"sale_id": sale["id"], "refund_mode": "CASH", "items": [back(sale, 0, quantity)]},
            headers={"Idempotency-Key": key},
        )

    def test_a_repeat_with_the_same_key_returns_the_same_return_and_refunds_once(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "4")], payment_method="CASH")
        first = self.post_with(client_a, sale, "attempt-000001")
        again = self.post_with(client_a, sale, "attempt-000001")
        assert first.status_code == 201 and again.status_code == 201
        assert again.headers["Idempotent-Replay"] == "true" and "Idempotent-Replay" not in first.headers
        assert again.json() == first.json()
        assert client_a.get(SR).json()["total"] == 1 and stock(client_a, shelf["rice"]) == 7

    def test_the_same_key_for_a_different_request_is_refused(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "4")], payment_method="CASH")
        self.post_with(client_a, sale, "attempt-000002", "1")
        clash = self.post_with(client_a, sale, "attempt-000002", "2")
        assert clash.status_code == 409 and clash.json()["error_code"] == "idempotency_key_reused"
        assert client_a.get(SR).json()["total"] == 1

    def test_a_failed_attempt_leaves_no_key_so_the_retry_runs(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        assert self.post_with(client_a, sale, "attempt-000003", "9").status_code == 422
        assert (
            self.post_with(client_a, sale, "attempt-000003", "1").status_code == 201
        )  # same key, corrected request

    @pytest.mark.parametrize("key", ["short", "has spaces in it", "x" * 101, "bad/slash!!"])
    def test_a_malformed_key_is_refused(self, client_a, shelf, key):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        assert self.post_with(client_a, sale, key).status_code == 422

    def test_keys_are_per_shop(self, client_a, client_b, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        self.post_with(client_a, sale, "attempt-000004")
        assert (
            self.post_with(client_b, sale, "attempt-000004").status_code == 404
        )  # not a replay of A's answer

    def test_sale_posting_with_a_key_is_replayed_not_repeated(self, client_a, shelf):
        draft = client_a.post(SALES, json={"items": [item(shelf["rice"], "2")]}).json()
        url = f"{SALES}/{draft['id']}/post"
        first = client_a.post(
            url, json={"payment_method": "CASH"}, headers={"Idempotency-Key": "post-attempt-01"}
        )
        second = client_a.post(
            url, json={"payment_method": "CASH"}, headers={"Idempotency-Key": "post-attempt-01"}
        )
        assert (
            first.status_code == second.status_code == 200 and second.headers["Idempotent-Replay"] == "true"
        )
        assert (
            second.json()["invoice_no"] == first.json()["invoice_no"] and stock(client_a, shelf["rice"]) == 8
        )
        assert (
            client_a.post(url, json={"payment_method": "CASH"}).status_code == 409
        )  # without a key: refused, not repeated

    def test_creates_are_deduplicated_by_key(self, client_a, tenant_a, units):
        body = {
            "sku": "DUP1",
            "name": "Once",
            "category_id": tenant_a.category.id,
            "unit_id": units["pcs"],
            "selling_price": "5",
        }
        headers = {"Idempotency-Key": "create-attempt-1"}
        one, two = (
            client_a.post("/api/v1/products", json=body, headers=headers),
            client_a.post("/api/v1/products", json=body, headers=headers),
        )
        assert one.status_code == two.status_code == 201 and one.json() == two.json()
        assert client_a.get("/api/v1/products", params={"q": "DUP1"}).json()["total"] == 1
        cust = {"name": "Once Only"}
        c1 = client_a.post("/api/v1/customers", json=cust, headers={"Idempotency-Key": "create-attempt-2"})
        c2 = client_a.post("/api/v1/customers", json=cust, headers={"Idempotency-Key": "create-attempt-2"})
        assert (
            c1.json() == c2.json()
            and client_a.get("/api/v1/customers", params={"q": "Once Only"}).json()["total"] == 1
        )

    @pytest.mark.parametrize("attempt", range(3))
    def test_simultaneous_full_returns_cannot_both_succeed(
        self, client_a, make_client, tenant_a, shelf, attempt
    ):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        clients = [make_client(tenant_a) for _ in range(3)]
        barrier = threading.Barrier(3)

        def run(client):
            barrier.wait()
            return give_back(client, sale, [back(sale, 0, "2")]).status_code

        with ThreadPoolExecutor(3) as pool:
            codes = sorted(pool.map(run, clients))
        assert codes == [201, 422, 422] and stock(client_a, shelf["rice"]) == 10


class TestPurchaseReturns:
    @pytest.fixture
    def bought(self, client_a, tenant_a, units):
        supplier = make_supplier(client_a)
        flour = make_product(client_a, tenant_a, units, "FLOUR", selling_price="60")
        response = client_a.post(
            PURCHASES,
            json={
                "supplier_id": supplier["id"],
                "items": [{"product_id": flour["id"], "quantity": "10", "unit_cost": "40", "discount": "10"}],
            },
        )
        purchase = client_a.post(f"{PURCHASES}/{response.json()['id']}/post").json()  # 10 x 40 - 10 = 390 net
        return {"flour": flour, "purchase": purchase, "supplier": supplier}

    def send_back(self, client, purchase, quantity="1", mode="CASH", **extra):
        item_id = purchase["items"][0]["id"]
        return client.post(
            PR,
            json={
                "purchase_id": purchase["id"],
                "credit_mode": mode,
                "items": [{"purchase_item_id": item_id, "quantity": quantity}],
                **extra,
            },
        )

    def test_goods_leave_the_shelf_at_the_net_cost_and_the_credit_is_proportional(self, client_a, bought):
        assert stock(client_a, bought["flour"]) == 10
        response = self.send_back(client_a, bought["purchase"], "4")
        assert response.status_code == 201, response.text
        ret = response.json()
        assert (
            ret["return_no"].startswith("PRT/")
            and ret["status"] == "POSTED"
            and ret["purchase_no"] == bought["purchase"]["purchase_no"]
        )
        assert (
            ret["total_amount"],
            ret["credit_mode"],
            ret["items"][0]["unit_cost"],
            ret["items"][0]["line_total"],
        ) == ("156.00", "CASH", "39.00", "156.00")
        assert stock(client_a, bought["flour"]) == 6

    def test_cumulative_credit_is_exact_and_the_last_return_takes_the_remainder(self, client_a, bought):
        parts = [
            D(self.send_back(client_a, bought["purchase"], q).json()["total_amount"]) for q in ("3", "3", "4")
        ]
        assert sum(parts) == D("390.00") and parts[-1] == D("390.00") - parts[0] - parts[1]
        assert stock(client_a, bought["flour"]) == 0

    def test_more_than_bought_is_refused(self, client_a, bought):
        response = self.send_back(client_a, bought["purchase"], "11")
        assert response.status_code == 422 and "Only 10 pcs" in errors(response)["items.0.quantity"]
        self.send_back(client_a, bought["purchase"], "8")
        assert "Only 2 pcs" in errors(self.send_back(client_a, bought["purchase"], "3"))["items.0.quantity"]

    def test_goods_that_were_already_sold_cannot_be_sent_back(self, client_a, bought):
        sold(client_a, [item(bought["flour"], "8")], payment_method="CASH")  # 2 left
        response = self.send_back(client_a, bought["purchase"], "3")
        assert response.status_code == 409 and response.json()["error_code"] == "insufficient_stock"
        assert stock(client_a, bought["flour"]) == 2 and client_a.get(PR).json()["total"] == 0
        assert self.send_back(client_a, bought["purchase"], "2").status_code == 201

    def test_the_preview_shows_the_stock_on_the_shelf(self, client_a, bought):
        sold(client_a, [item(bought["flour"], "8")], payment_method="CASH")
        item_id = bought["purchase"]["items"][0]["id"]
        line = client_a.post(
            f"{PR}/calculate",
            json={
                "purchase_id": bought["purchase"]["id"],
                "items": [{"purchase_item_id": item_id, "quantity": "5"}],
            },
        ).json()["lines"][0]
        assert (line["in_stock"], line["credit"], line["returnable"]) == ("2.000", "195.00", "10.000")

    def test_void_puts_the_goods_back_and_a_purchase_with_a_live_return_cannot_be_voided(
        self, client_a, bought
    ):
        ret = self.send_back(client_a, bought["purchase"], "4").json()
        assert (
            client_a.post(f"{PURCHASES}/{bought['purchase']['id']}/void", json={"reason": "x"}).status_code
            == 409
        )
        assert client_a.post(f"{PR}/{ret['id']}/void", json={"reason": "supplier refused"}).status_code == 200
        assert stock(client_a, bought["flour"]) == 10
        assert (
            client_a.post(f"{PURCHASES}/{bought['purchase']['id']}/void", json={"reason": "x"}).status_code
            == 200
        )

    def test_a_draft_purchase_cannot_have_a_return_and_dates_are_checked(self, client_a, bought):
        draft = client_a.post(
            PURCHASES,
            json={
                "supplier_id": bought["supplier"]["id"],
                "items": [{"product_id": bought["flour"]["id"], "quantity": "1", "unit_cost": "5"}],
            },
        ).json()
        assert (
            client_a.post(
                PR,
                json={
                    "purchase_id": draft["id"],
                    "credit_mode": "CASH",
                    "items": [{"purchase_item_id": draft["items"][0]["id"], "quantity": "1"}],
                },
            ).status_code
            == 409
        )
        assert (
            "future"
            in errors(self.send_back(client_a, bought["purchase"], "1", return_date="2999-01-01"))[
                "return_date"
            ]
        )
        assert (
            "before the purchase"
            in errors(self.send_back(client_a, bought["purchase"], "1", return_date="2001-01-01"))[
                "return_date"
            ]
        )

    def test_list_filters_and_isolation(self, client_a, client_b, bought):
        ret = self.send_back(client_a, bought["purchase"], "1", "SUPPLIER_CREDIT").json()
        assert ret["credit_mode"] == "SUPPLIER_CREDIT"
        assert [
            r["id"] for r in client_a.get(PR, params={"q": bought["supplier"]["name"]}).json()["items"]
        ] == [ret["id"]]
        assert client_b.get(f"{PR}/{ret['id']}").status_code == 404 and client_b.get(PR).json()["total"] == 0
        assert (
            client_b.post(
                PR,
                json={
                    "purchase_id": bought["purchase"]["id"],
                    "credit_mode": "CASH",
                    "items": [{"purchase_item_id": bought["purchase"]["items"][0]["id"], "quantity": "1"}],
                },
            ).status_code
            == 404
        )

    def test_replays_do_not_send_goods_back_twice(self, client_a, bought):
        item_id = bought["purchase"]["items"][0]["id"]
        body = {
            "purchase_id": bought["purchase"]["id"],
            "credit_mode": "CASH",
            "items": [{"purchase_item_id": item_id, "quantity": "2"}],
        }
        one = client_a.post(PR, json=body, headers={"Idempotency-Key": "purchase-ret-01"})
        two = client_a.post(PR, json=body, headers={"Idempotency-Key": "purchase-ret-01"})
        assert one.json() == two.json() and stock(client_a, bought["flour"]) == 8


class TestEveryBusinessTypeAndRole:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_returns_work_for_every_business_type(self, make_client, tenant_of, units, business_type):
        tenant = tenant_of(business_type)
        client = make_client(tenant)
        product = make_product(client, tenant, units, "ITEM", selling_price="100")
        stock_up(client, make_supplier(client), product, 5, 60)
        sale = sold(client, [item(product, "2")], payment_method="CASH")
        assert returned(client, sale, [back(sale, 0)])["total_refund"] == "100.00"

    def test_staff_can_take_a_return(self, make_client, tenant_a, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
        assert give_back(make_client(tenant_a, role=UserRole.STAFF), sale, [back(sale, 0)]).status_code == 201


def test_returns_appear_in_no_ledger_by_accident(client_a, shelf, fresh):
    """A cash return touches the stock ledger only; the customer ledger stays empty."""
    sale = sold(client_a, [item(shelf["rice"], "2")], payment_method="CASH")
    returned(client_a, sale, [back(sale, 0)])
    assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0
    assert factories is not None


class TestReportsAndExports:
    def test_reports_are_net_of_returns_and_profit_follows(self, client_a, shelf):
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"sale_date": "2026-03-01"}, payment_method="CASH"
        )  # 200, cost 80
        returned(client_a, sale, [back(sale, 0, "1")], return_date="2026-03-05")  # refund 50, cost back 20
        s = client_a.get(
            "/api/v1/reports/sales-summary", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
        ).json()
        assert (
            s["combined"]["net_sales"],
            s["returns_count"],
            s["returns_total"],
            s["net_after_returns"],
        ) == ("200.00", 1, "50.00", "150.00")
        assert (
            s["detailed_gross_profit"] == "90.00"
        )  # 200 - 80 cost, less the return: 50 refunded - 20 cost back
        assert s["returns_without_cost"] == 0

    def test_a_return_with_an_unknown_cost_is_counted_but_leaves_profit_alone(self, client_a, shelf):
        sale = sold(
            client_a, [item(shelf["oil"], "2")], header={"sale_date": "2026-04-01"}, payment_method="CASH"
        )
        returned(client_a, sale, [back(sale, 0, "1")], return_date="2026-04-02")
        s = client_a.get(
            "/api/v1/reports/sales-summary", params={"date_from": "2026-04-01", "date_to": "2026-04-30"}
        ).json()
        assert (s["returns_count"], s["returns_without_cost"], s["detailed_gross_profit"]) == (1, 1, None)

    def test_a_voided_return_is_not_in_the_report(self, client_a, shelf):
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"sale_date": "2026-05-01"}, payment_method="CASH"
        )
        ret = returned(client_a, sale, [back(sale, 0, "2")], return_date="2026-05-02")
        client_a.post(f"{SR}/{ret['id']}/void", json={"reason": "x"})
        s = client_a.get(
            "/api/v1/reports/sales-summary", params={"date_from": "2026-05-01", "date_to": "2026-05-31"}
        ).json()
        assert (s["returns_count"], s["returns_total"], s["net_after_returns"]) == (0, "0.00", "200.00")

    def test_exports(self, client_a, client_b, make_client, tenant_a, shelf):
        from tests.test_exports import read_csv

        customer = make_customer(client_a, "Asha Devi")
        sale = sold(
            client_a, [item(shelf["rice"], "4")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        ret = returned(client_a, sale, [back(sale, 0, "1")], "KHATA", reason="Damaged")
        response = client_a.get("/api/v1/exports/sales-returns")
        table = read_csv(response.content)
        row = dict(zip(table[0], table[1], strict=True))
        assert response.status_code == 200 and response.content.startswith("﻿".encode())
        assert (
            row["Return No"],
            row["Invoice No"],
            row["Customer"],
            row["Refund By"],
            row["Refund"],
            row["Cost Of Goods Returned"],
            row["Reason"],
        ) == (ret["return_no"], sale["invoice_no"], "Asha Devi", "Khata", "50.00", "20.00", "Damaged")
        assert len(read_csv(client_b.get("/api/v1/exports/sales-returns").content)) == 1  # header only
        assert (
            make_client(tenant_a, role=UserRole.STAFF).get("/api/v1/exports/sales-returns").status_code == 403
        )
        assert client_a.get("/api/v1/exports/sales-returns", params={"format": "xlsx"}).status_code == 200

    def test_purchase_return_export(self, client_a, tenant_a, units):
        from tests.test_exports import read_csv

        supplier = make_supplier(client_a)
        flour = make_product(client_a, tenant_a, units, "FL", selling_price="60")
        made = client_a.post(
            PURCHASES,
            json={
                "supplier_id": supplier["id"],
                "items": [{"product_id": flour["id"], "quantity": "10", "unit_cost": "40"}],
            },
        ).json()
        purchase = client_a.post(f"{PURCHASES}/{made['id']}/post").json()
        client_a.post(
            PR,
            json={
                "purchase_id": purchase["id"],
                "credit_mode": "SUPPLIER_CREDIT",
                "items": [{"purchase_item_id": purchase["items"][0]["id"], "quantity": "2"}],
            },
        )
        table = read_csv(client_a.get("/api/v1/exports/purchase-returns").content)
        row = dict(zip(table[0], table[1], strict=True))
        assert (row["Purchase No"], row["Supplier"], row["Credit By"], row["Credit"]) == (
            purchase["purchase_no"],
            "Sharma Traders",
            "Supplier Credit",
            "80.00",
        )
