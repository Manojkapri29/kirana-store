# ruff: noqa: F811
"""Phase 20 system QA. Business journeys run end to end over HTTP, and the accounting identities are recomputed independently from raw
ledger rows (not through the services being checked) and compared with what the application reports."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import CustomerLedgerEntry, InventoryTransaction
from app.reporting import integrity
from tests.factories import today_in_shop_timezone
from tests.test_purchases_api import posted, stock
from tests.test_returns import PR, back, returned
from tests.test_sales_api import CUSTOMERS, make_customer, owed, shelf, sold  # noqa: F401

D = Decimal
TODAY = today_in_shop_timezone()
FIN = "/api/v1/finance"


@pytest.fixture(autouse=True)
def _pro(give_plan, tenant_a):
    give_plan(tenant_a, "pro")


def _ledger_stock(session, shop_id, product_id) -> Decimal:
    session.commit()
    return session.scalar(
        select(func.coalesce(func.sum(InventoryTransaction.qty_delta), 0)).where(
            InventoryTransaction.shop_id == shop_id, InventoryTransaction.product_id == product_id
        )
    )


def _khata_sum(session, shop_id, customer_id) -> Decimal:
    session.commit()
    rows = session.scalars(
        select(CustomerLedgerEntry.amount_delta).where(
            CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.customer_id == customer_id
        )
    ).all()
    return sum((D(r) for r in rows), D("0"))


def _clean(session_factory):
    with session_factory() as s:
        assert integrity.run(s) == []


class TestJourneys:
    def test_1_shop_purchase_sale_khata_payment_reports(self, client_a, tenant_a, shelf, session, session_factory):
        rice = shelf["rice"]
        assert stock(client_a, rice) == 10  # purchase posted by the fixture
        customer = make_customer(client_a)
        sold(client_a, [{"product_id": rice["id"], "quantity": "4"}], header={"customer_id": customer["id"]}, amount_paid="50")
        assert stock(client_a, rice) == 6
        assert owed(client_a, customer) == D("150.00")  # 200 sale - 50 paid
        pay = client_a.post(f"{CUSTOMERS}/{customer['id']}/payments", json={"amount": "100", "payment_method": "UPI"})
        assert pay.status_code == 201
        assert owed(client_a, customer) == D("50.00")
        assert _khata_sum(session, tenant_a.shop.id, customer["id"]) == D("50.00")
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert D(pnl["detailed_sales"]) == D("200.00")
        _clean(session_factory)

    def test_2_quick_sale(self, client_a, tenant_a, session_factory):
        made = client_a.post("/api/v1/quick-sales", json={"gross_amount": "120"})
        assert made.status_code == 201, made.text
        done = client_a.post(f"/api/v1/quick-sales/{made.json()['id']}/post", json={"payment_method": "CASH"})
        assert done.status_code == 200, done.text
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert D(pnl["quick_sales"]) == D("120.00")
        # a quick sale carries no cost: profit is NOT invented
        assert pnl["quick_sales_have_no_cost"] is True
        _clean(session_factory)

    def test_3_online_order_through_the_public_store(self, client_a, tenant_a, shelf, session, real_client):
        """Added after the first audit found no order module: a customer orders, the shop delivers, one ordinary sale results."""
        client_a.put("/api/v1/store/settings", json={"slug": "qa-store", "display_name": "QA", "is_open": True})
        client_a.put(f"/api/v1/store/listings/{shelf['rice']['id']}", json={"visible": True})
        order = real_client().post(
            "/api/v1/public/stores/qa-store/orders",
            json={"customer_name": "Asha", "customer_phone": "9876500001", "fulfilment": "PICKUP", "payment": "COD", "items": [{"product_id": shelf["rice"]["id"], "quantity": "2"}]},
            headers={"Idempotency-Key": "journey-order-0001"},
        ).json()
        assert order["total_amount"] == "100.00" and stock(client_a, shelf["rice"]) == 10  # a request: nothing moved
        oid = client_a.get("/api/v1/online-orders").json()["items"][0]["id"]
        client_a.post(f"/api/v1/online-orders/{oid}/accept")
        client_a.post(f"/api/v1/online-orders/{oid}/advance", json={"status": "READY"})
        done = client_a.post(f"/api/v1/online-orders/{oid}/advance", json={"status": "DELIVERED"}).json()
        assert done["invoice_no"] and stock(client_a, shelf["rice"]) == 8
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert D(pnl["detailed_sales"]) == D("100.00")

    def test_4_customer_loyalty_crm(self, client_a, tenant_a, shelf, session_factory):
        assert client_a.put(
            "/api/v1/loyalty/program",
            json={"is_active": True, "points_per_amount": "0.1", "redemption_value": "1"},
        ).status_code == 200
        customer = make_customer(client_a, name="Asha")
        sold(client_a, [{"product_id": shelf["rice"]["id"], "quantity": "2"}], header={"customer_id": customer["id"]})
        led = client_a.get(f"/api/v1/loyalty/customers/{customer['id']}/ledger").json()
        assert led["items"] and sum(e["points_delta"] for e in led["items"]) == 10  # 100 rupees x 0.1
        prof = client_a.get(f"/api/v1/crm/customers/{customer['id']}/profile")
        assert prof.status_code == 200
        _clean(session_factory)

    def test_5_weighted_average_cost_cogs_and_gross_profit(self, client_a, shelf):
        sugar = shelf["sugar"]  # 100 kg @ 20 then 50 kg @ 30 -> 3500 / 150 = 23.33 average
        sale = sold(client_a, [{"product_id": sugar["id"], "quantity": "30"}])
        assert D(sale["total_amount"]) == D("1440.00")
        cogs = D(sale["cogs_total"])
        assert cogs == D("30") * D("23.33")  # the average is kept to 2 places (23.33), so 699.90, not 700.00
        assert D(sale["gross_profit"]) == D("1440.00") - cogs
        # a later purchase changes the average for FUTURE sales only; the posted sale keeps its cost
        posted(client_a, shelf["supplier"], [{"product_id": sugar["id"], "quantity": "120", "unit_cost": "40"}])
        again = client_a.get(f"/api/v1/sales/{sale['id']}").json()
        assert D(again["cogs_total"]) == cogs

    def test_6_expense_approval_pnl_and_cash(self, client_a, shelf):
        sold(client_a, [{"product_id": shelf["rice"]["id"], "quantity": "4"}])  # 200 cash in, cost 80
        before = client_a.get(f"{FIN}/cash/summary", params={"day": TODAY.isoformat()}).json()
        cat = client_a.post(f"{FIN}/expense-categories", json={"name": "Rent"}).json()
        e = client_a.post(
            f"{FIN}/expenses",
            json={"expense_date": TODAY.isoformat(), "category_id": cat["id"], "amount": "60.00", "payment_method": "CASH"},
        ).json()
        assert client_a.post(f"{FIN}/expenses/{e['id']}/submit").json()["status"] == "APPROVED"
        assert client_a.post(f"{FIN}/expenses/{e['id']}/post").json()["status"] == "POSTED"
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert D(pnl["operating_expenses"]) == D("60.00")
        assert D(pnl["net_profit"]) == D(pnl["gross_profit"]) - D("60.00")
        after = client_a.get(f"{FIN}/cash/summary", params={"day": TODAY.isoformat()}).json()
        assert D(after["net_movement"]) == D(before["net_movement"]) - D("60.00")

    def test_7_offline_sale_sync(self, client_a, shelf):
        from tests.test_phase18_sync import op_id, sale_op, send

        oid = op_id()
        first = send(client_a, sale_op(shelf["rice"], "2", oid=oid))
        replay = send(client_a, sale_op(shelf["rice"], "2", oid=oid))
        assert first[0]["status"] == "SYNCED" and replay[0]["status"] == "SYNCED"
        assert stock(client_a, shelf["rice"]) == 8  # applied once

    def test_8_external_payment_webhook(self, client_a, session, tenant_a, monkeypatch):
        from tests.test_phase17_payments import _balance, _customer_owing, _event, _hook, _setup

        view = _setup(client_a, "generic_webhook", monkeypatch)
        customer = _customer_owing(session, tenant_a, "1000.00")
        p = client_a.post(
            "/api/v1/payments",
            json={"method": "ONLINE_PAYMENT", "purpose": "KHATA_PAYMENT", "amount": "250.00", "customer_id": customer.id, "provider_txn_id": "TXN-Q"},
            headers={"Idempotency-Key": "qa-pay-000001"},
        ).json()
        assert p["status"] == "PENDING"
        assert _hook(client_a, view, _event("TXN-Q", "CAPTURED", "evt-q1")).status_code == 200
        assert _hook(client_a, view, _event("TXN-Q", "CAPTURED", "evt-q1")).status_code == 200  # redelivery
        assert _balance(session, tenant_a.shop.id, customer.id) == D("750.00")  # exactly once


class TestIdentities:
    def test_inventory_khata_pnl_and_cash_identities(self, client_a, tenant_a, shelf, session, session_factory):
        shop = tenant_a.shop.id
        rice, sugar, oil = shelf["rice"], shelf["sugar"], shelf["oil"]
        customer = make_customer(client_a, opening_balance="300")
        bought = posted(client_a, shelf["supplier"], [{"product_id": rice["id"], "quantity": "20", "unit_cost": "25"}])
        s1 = sold(client_a, [{"product_id": rice["id"], "quantity": "5"}, {"product_id": sugar["id"], "quantity": "10"}], header={"customer_id": customer["id"]}, amount_paid="100")
        returned(client_a, s1, [back(s1, 0, "1")])
        pr = client_a.post(
            PR,
            json={"purchase_id": bought["id"], "credit_mode": "CASH", "items": [{"purchase_item_id": bought["items"][0]["id"], "quantity": "3"}]},
        )
        assert pr.status_code == 201, pr.text
        client_a.post(f"{CUSTOMERS}/{customer['id']}/payments", json={"amount": "40", "payment_method": "CASH"})

        # inventory = opening + purchases - sales + sales returns - purchase returns +/- adjustments, from the raw ledger
        session.commit()
        by_type = dict(
            session.execute(
                select(InventoryTransaction.txn_type, func.sum(InventoryTransaction.qty_delta))
                .where(InventoryTransaction.shop_id == shop, InventoryTransaction.product_id == rice["id"])
                .group_by(InventoryTransaction.txn_type)
            ).all()
        )
        get = lambda k: D(by_type.get(k) or 0)  # noqa: E731
        formula = get("OPENING") + get("PURCHASE") + get("SALE") + get("SALE_RETURN") + get("PURCHASE_RETURN") + get("ADJUSTMENT") + get("REVERSAL")
        assert stock(client_a, rice) == formula == D("10") + D("20") - D("5") + D("1") - D("3")
        for p in (rice, sugar, oil):
            assert stock(client_a, p) == _ledger_stock(session, shop, p["id"])

        # khata = the sum of the ledger, and the ledger sums to what the API says
        assert owed(client_a, customer) == _khata_sum(session, shop, customer["id"])
        expected = D("300") + (D("5") * D("50") + D("10") * D("48") - D("100")) - D("40")  # the return was refunded in CASH, so the khata is untouched by it
        assert owed(client_a, customer) == expected

        # profit and loss: net = revenue - cogs - expenses; revenue = sales - returns
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert D(pnl["revenue"]) == D(pnl["detailed_sales"]) + D(pnl["quick_sales"]) + D(pnl["online_sales"]) - D(pnl["sales_returns"])
        if pnl["cogs"] is not None:
            assert D(pnl["gross_profit"]) == D(pnl["revenue"]) - D(pnl["cogs"])
            assert D(pnl["net_profit"]) == D(pnl["gross_profit"]) - D(pnl["operating_expenses"])
        else:  # unknown cost is never shown as zero
            assert pnl["gross_profit"] is None and pnl["net_profit"] is None
        _clean(session_factory)

    def test_unknown_cost_is_never_zero(self, client_a, shelf):
        sold(client_a, [{"product_id": shelf["oil"]["id"], "quantity": "1"}])  # oil has no known cost
        pnl = client_a.get(f"{FIN}/pnl", params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}).json()
        assert pnl["cogs"] is None and pnl["net_profit"] is None and pnl["status"] != "OK"
