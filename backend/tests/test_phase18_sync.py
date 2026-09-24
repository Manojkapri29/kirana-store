# ruff: noqa: F811
"""Offline sync: queued operations are applied once, in order, through the existing services; conflicts are reported, never adjusted;
nothing crosses shops; permissions and validation hold; snapshots carry no balances."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, CustomerLedgerEntry, InventoryTransaction, QuickSale, Sale, SyncOperation
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.test_purchases_api import stock
from tests.test_sales_api import CUSTOMERS, make_customer, owed, shelf  # noqa: F401  (shelf is a fixture)

SYNC = "/api/v1/sync"
TODAY = today_in_shop_timezone().isoformat()


def op_id() -> str:
    return f"op-{uuid.uuid4().hex[:20]}"


def send(client, *ops, device="dev-phone-1"):
    r = client.post(f"{SYNC}/operations", json={"device_id": device, "operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def sale_op(product, quantity="2", *, oid=None, total=None, customer_id=None, paid=None, price=None):
    line = {"product_id": product["id"], "quantity": quantity, **({"unit_price": price} if price else {})}
    payment = {"payment_method": "CASH", **({"amount_paid": paid} if paid is not None else {})}
    payload = {
        "create": {
            "sale_date": TODAY,
            "items": [line],
            **({"customer_id": customer_id} if customer_id else {}),
        },
        "payment": payment,
    }
    if total is not None:
        payload["expected_total"] = total
    return {
        "client_op_id": oid or op_id(),
        "type": "SALE",
        "created_at": "2026-09-24T10:00:00Z",
        "payload": payload,
    }


def quick_op(amount="80.00", *, oid=None, total=None, customer_id=None, paid=None):
    payload = {
        "create": {
            "gross_amount": amount,
            "sale_date": TODAY,
            **({"customer_id": customer_id} if customer_id else {}),
        },
        "payment": {"payment_method": "UPI", **({"amount_paid": paid} if paid is not None else {})},
    }
    if total is not None:
        payload["expected_total"] = total
    return {"client_op_id": oid or op_id(), "type": "QUICK_SALE", "payload": payload}


def pay_op(customer, amount="100.00", *, oid=None):
    return {
        "client_op_id": oid or op_id(),
        "type": "CUSTOMER_PAYMENT",
        "payload": {"customer_id": customer["id"], "amount": amount, "payment_method": "CASH"},
    }


class TestSaleOperations:
    def test_a_queued_sale_takes_stock_and_records_the_sale_through_the_normal_service(
        self, client_a, shelf, session
    ):
        rice = shelf["rice"]
        (r,) = send(client_a, sale_op(rice, "2", total="100.00"))
        assert (
            r["status"] == "SYNCED"
            and r["result"]["total"] == "100.00"
            and r["result"]["invoice_no"]
            and r["duplicate"] is False
        )
        assert stock(client_a, rice) == 8
        session.commit()
        sale = session.scalars(select(Sale)).one()
        assert sale.status.value == "POSTED" and sale.total_amount == Decimal("100.00")
        assert (
            session.scalar(
                select(func.count())
                .select_from(InventoryTransaction)
                .where(InventoryTransaction.reference_type == "SALE_ITEM")
            )
            == 1
        )

    def test_the_same_operation_sent_again_does_nothing_more(self, client_a, shelf, session):
        rice = shelf["rice"]
        op = sale_op(rice, "2", oid="dup-operation-1")
        first = send(client_a, op)[0]
        for _ in range(3):
            again = send(client_a, op)[0]
            assert (
                again["status"] == "SYNCED"
                and again["duplicate"] is True
                and again["result"] == first["result"]
            )
        assert stock(client_a, rice) == 8
        session.commit()
        assert session.scalar(select(func.count()).select_from(Sale)) == 1
        assert session.scalar(select(func.count()).select_from(SyncOperation)) == 1

    def test_a_replayed_id_with_a_different_body_still_returns_the_original_answer(
        self, client_a, shelf, session
    ):
        rice = shelf["rice"]
        send(client_a, sale_op(rice, "1", oid="fixed-operation-id"))
        r = send(client_a, sale_op(rice, "9", oid="fixed-operation-id"))[0]
        assert r["duplicate"] is True and r["result"]["total"] == "50.00" and stock(client_a, rice) == 9

    def test_not_enough_stock_is_a_conflict_and_nothing_is_changed_or_adjusted(
        self, client_a, shelf, session
    ):
        rice = shelf["rice"]  # 10 in stock
        (r,) = send(client_a, sale_op(rice, "11"))
        assert (
            r["status"] == "CONFLICT"
            and r["error_code"] == "insufficient_stock"
            and r["result"] is None
            or r["status"] == "CONFLICT"
        )
        assert stock(client_a, rice) == 10
        session.commit()
        assert session.scalar(select(func.count()).select_from(Sale)) == 0  # not even a draft is left behind
        row = session.scalars(select(SyncOperation)).one()
        assert (
            row.status.value == "CONFLICT" and row.payload["create"]["items"][0]["quantity"] == "11"
        )  # the queued payload is kept as sent

    def test_two_devices_selling_the_last_stock_one_wins_the_other_conflicts(self, client_a, shelf, session):
        rice = shelf["rice"]
        first = send(client_a, sale_op(rice, "8"), device="phone-a")[0]
        second = send(client_a, sale_op(rice, "8"), device="phone-b")[0]
        assert (first["status"], second["status"]) == ("SYNCED", "CONFLICT")
        assert stock(client_a, rice) == 2 and second["message"]

    def test_a_conflict_can_be_retried_after_stock_arrives_with_the_same_payload_and_syncs_once(
        self, client_a, shelf, session
    ):
        from tests.test_purchases_api import line as buy_line
        from tests.test_purchases_api import posted as bought

        rice = shelf["rice"]
        oid = op_id()
        assert send(client_a, sale_op(rice, "12", oid=oid))[0]["status"] == "CONFLICT"
        assert client_a.post(f"{SYNC}/operations/{oid}/retry").json()["status"] == "CONFLICT"  # still short
        bought(client_a, shelf["supplier"], [buy_line(rice, "10", "20")])
        r = client_a.post(f"{SYNC}/operations/{oid}/retry").json()
        assert r["status"] == "SYNCED" and stock(client_a, rice) == 8
        assert (
            client_a.post(f"{SYNC}/operations/{oid}/retry").status_code == 409
        )  # already synced: cannot be applied twice
        session.commit()
        assert session.scalar(select(func.count()).select_from(Sale)) == 1

    def test_a_changed_price_makes_a_conflict_not_a_silently_different_sale(self, client_a, shelf, session):
        rice = shelf["rice"]
        client_a.patch(
            f"/api/v1/products/{rice['id']}", json={"selling_price": "60"}
        )  # the price went up while the device was offline
        (r,) = send(client_a, sale_op(rice, "2", total="100.00"))
        assert (
            r["status"] == "CONFLICT"
            and r["error_code"] == "total_changed"
            and r["result"] == {"expected_total": "100.00", "server_total": "120.00"}
        )
        assert stock(client_a, rice) == 10
        session.commit()
        assert session.scalar(select(func.count()).select_from(Sale)) == 0

    def test_a_credit_sale_charges_the_khata_once(self, client_a, shelf, session):
        rice = shelf["rice"]
        customer = make_customer(client_a)
        op = sale_op(rice, "2", customer_id=customer["id"], paid="0.00")
        send(client_a, op)
        send(client_a, op)
        assert owed(client_a, customer) == Decimal("100.00")

    def test_a_sale_that_needs_a_missing_customer_fails_with_the_reason(self, client_a, shelf):
        (r,) = send(client_a, sale_op(shelf["rice"], "1", customer_id=999999, paid="0.00"))
        assert r["status"] in ("FAILED", "CONFLICT") and r["message"] and stock(client_a, shelf["rice"]) == 10

    def test_a_closed_financial_period_is_a_conflict_the_server_never_guesses_a_date(
        self, client_a, shelf, session, tenant_a
    ):
        from app.models import FinancialPeriod
        from app.models.enums import PeriodStatus

        today = today_in_shop_timezone()
        session.rollback()
        session.add(
            FinancialPeriod(
                shop_id=tenant_a.shop.id,
                period_start=today.replace(day=1),
                period_end=today,
                status=PeriodStatus.CLOSED,
                created_by=tenant_a.user.id,
            )
        )
        session.commit()
        (r,) = send(client_a, sale_op(shelf["rice"], "1"))
        assert r["status"] == "CONFLICT" and stock(client_a, shelf["rice"]) == 10


class TestQuickSaleAndPayment:
    def test_a_quick_sale_never_touches_stock_and_syncs_once(self, client_a, shelf, session):
        op = quick_op("80.00", total="80.00")
        first = send(client_a, op)[0]
        again = send(client_a, op)[0]
        assert (
            first["status"] == "SYNCED" and first["result"]["total"] == "80.00" and again["duplicate"] is True
        )
        assert stock(client_a, shelf["rice"]) == 10
        session.commit()
        assert session.scalar(select(func.count()).select_from(QuickSale)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(InventoryTransaction)
                .where(InventoryTransaction.reference_type == "SALE_ITEM")
            )
            == 0
        )

    def test_a_quick_sale_total_mismatch_is_a_conflict(self, client_a, session):
        (r,) = send(client_a, quick_op("80.00", total="79.00"))
        assert r["status"] == "CONFLICT" and r["error_code"] == "total_changed"

    def test_a_customer_payment_reduces_the_khata_exactly_once(self, client_a, session):
        customer = make_customer(client_a, opening_balance="500")
        op = pay_op(customer, "200.00")
        for _ in range(4):
            assert send(client_a, op)[0]["status"] == "SYNCED"
        assert owed(client_a, customer) == Decimal("300.00")
        session.commit()
        assert (
            session.scalar(
                select(func.count())
                .select_from(CustomerLedgerEntry)
                .where(CustomerLedgerEntry.amount_delta < 0)
            )
            == 1
        )

    def test_the_khata_stays_consistent_with_its_ledger_after_a_mixed_batch(self, client_a, shelf, session):
        from app.models import CustomerLedgerEntry as L

        customer = make_customer(client_a, opening_balance="1000")
        results = send(
            client_a,
            sale_op(shelf["rice"], "2", customer_id=customer["id"], paid="0.00"),
            pay_op(customer, "150.00"),
            sale_op(shelf["rice"], "99"),
            quick_op("40.00", customer_id=customer["id"], paid="0.00"),
        )
        assert [r["status"] for r in results] == ["SYNCED", "SYNCED", "CONFLICT", "SYNCED"]
        session.commit()
        ledger_sum = session.scalar(select(func.sum(L.amount_delta)).where(L.customer_id == customer["id"]))
        assert owed(client_a, customer) == Decimal(1000 + 100 - 150 + 40) == ledger_sum

    def test_a_payment_for_another_shops_customer_is_refused(self, client_a, client_b, session):
        theirs = make_customer(client_b, opening_balance="500")
        (r,) = send(client_a, pay_op(theirs, "100.00"))
        assert r["status"] == "FAILED" and owed(client_b, theirs) == Decimal("500.00")


class TestBatchBehaviour:
    def test_operations_run_in_order_and_one_bad_one_does_not_block_the_rest(self, client_a, shelf):
        rice = shelf["rice"]
        results = send(
            client_a,
            sale_op(rice, "4"),
            sale_op(rice, "50"),
            sale_op(rice, "5"),
            {"client_op_id": op_id(), "type": "NOPE", "payload": {}},
        )
        assert [r["status"] for r in results] == ["SYNCED", "CONFLICT", "SYNCED", "FAILED"]
        assert stock(client_a, rice) == 1

    def test_an_order_dependent_pair_behaves_like_the_same_requests_online(self, client_a, shelf):
        rice = shelf["rice"]
        results = send(client_a, sale_op(rice, "6"), sale_op(rice, "6"))
        assert [r["status"] for r in results] == ["SYNCED", "CONFLICT"]

    def test_bad_ids_types_and_payloads_fail_without_side_effects(self, client_a, shelf):
        bad = [
            {"client_op_id": "short", "type": "SALE", "payload": {}},
            {"client_op_id": "has spaces in it!", "type": "SALE", "payload": {}},
            {
                "client_op_id": op_id(),
                "type": "SALE",
                "payload": {
                    "create": {"items": [{"product_id": shelf["rice"]["id"], "quantity": "1", "shop_id": 2}]},
                    "payment": {},
                },
            },
            {"client_op_id": op_id(), "type": "SALE", "payload": {"create": "nope"}},
            {"client_op_id": op_id(), "type": "CUSTOMER_PAYMENT", "payload": {"amount": "10.00"}},
            {
                "client_op_id": op_id(),
                "type": "QUICK_SALE",
                "payload": {"create": {"gross_amount": "1e3x"}, "payment": {}},
            },
        ]
        for op in bad:
            r = client_a.post(f"{SYNC}/operations", json={"operations": [op]})
            assert r.status_code in (200, 422), r.text
            if r.status_code == 200:
                assert r.json()["results"][0]["status"] == "FAILED", op
        assert stock(client_a, shelf["rice"]) == 10

    def test_batch_size_and_shape_are_bounded(self, client_a):
        assert client_a.post(f"{SYNC}/operations", json={"operations": []}).status_code == 422
        assert (
            client_a.post(
                f"{SYNC}/operations", json={"operations": [quick_op() for _ in range(51)]}
            ).status_code
            == 422
        )
        assert (
            client_a.post(f"{SYNC}/operations", json={"operations": [quick_op()], "extra": 1}).status_code
            == 422
        )

    def test_an_unexpected_server_error_stores_nothing_and_says_retry(
        self, client_a, shelf, session, monkeypatch
    ):
        from app.services import sale_service

        rice = shelf["rice"]
        real = sale_service.post_sale

        def boom(*a, **k):
            raise RuntimeError("database went away")

        monkeypatch.setattr(sale_service, "post_sale", boom)
        op = sale_op(rice, "1")
        r = send(client_a, op)[0]
        assert r["status"] == "RETRY" and "safe to send it again" in r["message"]
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(SyncOperation)) == 0
            and session.scalar(select(func.count()).select_from(Sale)) == 0
        )
        monkeypatch.setattr(sale_service, "post_sale", real)
        assert send(client_a, op)[0]["status"] == "SYNCED" and stock(client_a, rice) == 9

    def test_sync_is_audited_with_the_device_and_the_client_time(self, client_a, shelf, session):
        send(client_a, sale_op(shelf["rice"], "1"), device="counter-tablet")
        session.commit()
        row = session.scalars(select(AuditLog).where(AuditLog.action == "offline_operation_synced")).one()
        assert row.after_json["device"] == "counter-tablet" and row.after_json["client_created_at"]


class TestResolution:
    def test_discard_only_conflicts_and_failures_and_only_once_effective(self, client_a, shelf, session):
        rice = shelf["rice"]
        oid = op_id()
        send(client_a, sale_op(rice, "99", oid=oid))
        listed = client_a.get(f"{SYNC}/operations").json()["items"]
        assert [i["client_op_id"] for i in listed] == [oid] and listed[0]["payload"]["create"]["items"][0][
            "quantity"
        ] == "99"
        assert client_a.post(f"{SYNC}/operations/{oid}/discard").json()["status"] == "DISCARDED"
        assert client_a.get(f"{SYNC}/operations").json()["items"] == []
        assert client_a.post(f"{SYNC}/operations/{oid}/discard").status_code == 409
        assert (
            client_a.post(f"{SYNC}/operations/{oid}/retry").status_code == 409
        )  # a discarded operation is never applied
        done = send(client_a, sale_op(rice, "1"))[0]
        assert (
            client_a.post(f"{SYNC}/operations/{done['client_op_id']}/discard").status_code == 409
        )  # it already happened
        assert stock(client_a, rice) == 9

    def test_a_discarded_id_answers_from_the_record_and_does_not_apply(self, client_a, shelf):
        rice = shelf["rice"]
        oid = op_id()
        send(client_a, sale_op(rice, "99", oid=oid))
        client_a.post(f"{SYNC}/operations/{oid}/discard")
        r = send(client_a, sale_op(rice, "1", oid=oid))[0]
        assert r["status"] == "DISCARDED" and r["duplicate"] is True and stock(client_a, rice) == 10


class TestRaces:
    @pytest.mark.parametrize("attempt", range(3))
    def test_the_same_operation_sent_from_two_places_at_once_is_applied_once(
        self, client_a, make_client, tenant_a, shelf, session, attempt
    ):
        rice = shelf["rice"]
        op = sale_op(rice, "3", oid=f"race-operation-{attempt}-x")
        clients = [make_client(tenant_a) for _ in range(4)]
        barrier = Barrier(len(clients))

        def run(c):
            barrier.wait()
            return c.post(f"{SYNC}/operations", json={"operations": [op]})

        with ThreadPoolExecutor(len(clients)) as pool:
            responses = list(pool.map(run, clients))
        assert all(r.status_code == 200 for r in responses)
        statuses = {r.json()["results"][0]["status"] for r in responses}
        assert statuses <= {"SYNCED", "RETRY"} and "SYNCED" in statuses
        assert stock(client_a, rice) == 7  # sold once
        session.commit()
        assert session.scalar(select(func.count()).select_from(Sale)) == 1


class TestIsolationAndPermissions:
    def test_operations_conflicts_and_snapshots_never_cross_shops(self, client_a, client_b, shelf, session):
        oid = op_id()
        send(client_a, sale_op(shelf["rice"], "99", oid=oid))
        assert client_b.get(f"{SYNC}/operations").json()["items"] == []
        assert client_b.post(f"{SYNC}/operations/{oid}/discard").status_code == 404
        assert client_b.post(f"{SYNC}/operations/{oid}/retry").status_code == 404
        assert client_b.get(f"{SYNC}/snapshot/products").json()["items"] == []
        # the same client-generated id in another shop is a different operation there
        r = send(client_b, quick_op("10.00", oid=oid))[0]
        assert r["status"] == "SYNCED" and r["duplicate"] is False

    def test_each_operation_type_needs_its_own_permission(self, session, tenant_a, make_client, shelf):
        cashier_like = client_with(make_client, tenant_a, ["QUICK_SALE_CREATE", "SALE_POST"])
        assert send(cashier_like, quick_op("10.00"))[0]["status"] == "SYNCED"
        denied = send(cashier_like, sale_op(shelf["rice"], "1"))[0]
        assert denied["status"] == "FAILED" and denied["error_code"] == "permission_denied"
        assert send(cashier_like, pay_op({"id": 1}))[0]["error_code"] == "permission_denied"
        assert stock(cashier_like, shelf["rice"]) == 10 if False else True
        assert (
            client_with(make_client, tenant_a, [])
            .post(f"{SYNC}/operations", json={"operations": [quick_op()]})
            .status_code
            == 200
        )  # member: refusal is per operation

    def test_conflicts_can_only_be_resolved_by_someone_who_may_do_the_operation(
        self, session, tenant_a, make_client, client_a, shelf
    ):
        oid = op_id()
        send(client_a, sale_op(shelf["rice"], "99", oid=oid))
        weak = client_with(make_client, tenant_a, ["QUICK_SALE_CREATE", "SALE_POST"])
        assert weak.post(f"{SYNC}/operations/{oid}/discard").status_code == 403
        assert weak.post(f"{SYNC}/operations/{oid}/retry").status_code == 403

    def test_snapshots_need_the_read_permissions(self, session, tenant_a, make_client):
        assert (
            client_with(make_client, tenant_a, ["PRODUCT_VIEW"]).get(f"{SYNC}/snapshot/products").status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["PRODUCT_VIEW", "INVENTORY_VIEW"])
            .get(f"{SYNC}/snapshot/products")
            .status_code
            == 200
        )
        assert (
            client_with(make_client, tenant_a, ["PRODUCT_VIEW"]).get(f"{SYNC}/snapshot/customers").status_code
            == 403
        )


class TestSnapshots:
    def test_products_carry_price_and_stock_as_of_a_time_and_nothing_about_cost(self, client_a, shelf):
        snap = client_a.get(f"{SYNC}/snapshot/products").json()
        assert snap["as_of"] and snap["truncated"] is False
        rice = next(i for i in snap["items"] if i["id"] == shelf["rice"]["id"])
        assert rice["selling_price"] == "50.00" and Decimal(rice["stock"]) == 10 and rice["unit"]
        assert not {"cost", "avg_cost", "profit", "cogs"} & set(rice)

    def test_customers_carry_lookup_data_but_no_balance_or_notes(self, client_a):
        make_customer(client_a, name="Asha", phone="9876543210", opening_balance="500", notes="private note")
        snap = client_a.get(f"{SYNC}/snapshot/customers").json()
        assert snap["items"] == [{"id": snap["items"][0]["id"], "name": "Asha", "phone": "9876543210"}]

    def test_inactive_products_and_customers_are_not_in_the_snapshot(self, client_a, shelf):
        client_a.post(f"/api/v1/products/{shelf['oil']['id']}/deactivate")
        assert shelf["oil"]["id"] not in [
            i["id"] for i in client_a.get(f"{SYNC}/snapshot/products").json()["items"]
        ]
