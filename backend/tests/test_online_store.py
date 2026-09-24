# ruff: noqa: F811
"""The online store: public ordering is safe and honest, an order moves no stock or money until it is DELIVERED, delivery creates exactly
one ordinary posted sale, and nothing crosses shops."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import OnlineOrder, OnlineOrderEvent, Sale
from tests.client_helpers import client_with
from tests.test_purchases_api import stock
from tests.test_returns import back, returned
from tests.test_sales_api import owed, shelf  # noqa: F401  (shelf is a fixture)

D = Decimal
STORE = "/api/v1/store"
ORDERS = "/api/v1/online-orders"
PUBLIC = "/api/v1/public/stores"
SLUG = "ram-kirana"


def key() -> str:
    return f"key-{uuid.uuid4().hex[:16]}"


def open_store(client, slug=SLUG, **more):
    r = client.put(f"{STORE}/settings", json={"slug": slug, "display_name": "Ram Kirana", "is_open": True, **more})
    assert r.status_code == 200, r.text
    return r.json()


def list_products(client, *products):
    for p in products:
        r = client.put(f"{STORE}/listings/{p['id']}", json={"visible": True})
        assert r.status_code == 200, r.text


@pytest.fixture
def store(client_a, shelf):
    open_store(client_a)
    list_products(client_a, shelf["rice"], shelf["sugar"], shelf["oil"])
    return shelf


@pytest.fixture
def anon(real_client):
    return real_client()


def order_body(items, **over):
    body = {
        "customer_name": "Asha Verma",
        "customer_phone": "9876500001",
        "fulfilment": "DELIVERY",
        "payment": "COD",
        "delivery_address": "12 MG Road, Pune",
        "items": [{"product_id": p["id"], "quantity": q} for p, q in items],
    }
    body.update(over)
    return body


def place(anon, items, *, slug=SLUG, k=None, status=201, **over):
    r = anon.post(f"{PUBLIC}/{slug}/orders", json=order_body(items, **over), headers={"Idempotency-Key": k or key()})
    assert r.status_code == status, r.text
    return r.json()


def order_id(client, order):
    rows = client.get(ORDERS).json()["items"]
    return next(o["id"] for o in rows if o["order_no"] == order["order_no"])


def sale_count(session, shop_id):
    session.commit()
    return session.scalar(select(func.count()).select_from(Sale).where(Sale.shop_id == shop_id))


class TestSetupAndListing:
    def test_there_is_no_store_until_the_shop_sets_one_up(self, client_a):
        assert client_a.get(f"{STORE}/settings").json() == {"store": None}

    def test_the_address_is_required_valid_and_unique_across_shops(self, client_a, client_b):
        assert client_a.put(f"{STORE}/settings", json={"display_name": "X"}).status_code == 422
        for bad in ("ab", "Ram Kirana", "-ram", "ram--kirana", "admin", "api", "x" * 41, "ram_kirana"):
            r = client_a.put(f"{STORE}/settings", json={"slug": bad})
            assert r.status_code == 422, bad
        open_store(client_a)
        again = client_b.put(f"{STORE}/settings", json={"slug": SLUG, "display_name": "Other"})
        assert again.status_code == 409 and again.json()["error_code"] == "slug_taken"
        assert client_a.put(f"{STORE}/settings", json={"slug": SLUG}).status_code == 200  # keeping your own is fine

    def test_a_store_starts_closed_and_needs_a_way_to_pay_and_a_way_to_receive(self, client_a):
        made = client_a.put(f"{STORE}/settings", json={"slug": SLUG, "display_name": "Ram"}).json()
        assert made["is_open"] is False and made["public_path"] == f"/store/{SLUG}"
        assert client_a.put(f"{STORE}/settings", json={"accepts_cod": False, "accepts_upi": False}).status_code == 422
        assert client_a.put(f"{STORE}/settings", json={"delivery_enabled": False, "pickup_enabled": False}).status_code == 422

    def test_only_products_the_shop_chose_are_listed_and_never_another_shops(self, client_a, client_b, shelf, tenant_b, units):
        open_store(client_a)
        assert client_a.get(f"{STORE}/listings", params={"only_visible": True}).json()["total"] == 0
        list_products(client_a, shelf["rice"])
        assert [r["name"] for r in client_a.get(f"{STORE}/listings", params={"only_visible": True}).json()["items"]] == [shelf["rice"]["name"]]
        assert client_b.put(f"{STORE}/listings/{shelf['rice']['id']}", json={"visible": True}).status_code == 404
        assert client_a.put(f"{STORE}/listings/{shelf['rice']['id']}", json={"visible": False}).json()["is_visible"] is False

    def test_an_inactive_product_cannot_be_shown(self, client_a, shelf):
        open_store(client_a)
        client_a.post(f"/api/v1/products/{shelf['oil']['id']}/deactivate")
        assert client_a.put(f"{STORE}/listings/{shelf['oil']['id']}", json={"visible": True}).status_code == 422


class TestPublicCatalogue:
    def test_anyone_can_see_the_store_and_its_products_without_signing_in(self, anon, store):
        info = anon.get(f"{PUBLIC}/{SLUG}").json()
        assert info["name"] == "Ram Kirana" and info["is_open"] is True
        page = anon.get(f"{PUBLIC}/{SLUG}/products").json()
        assert page["total"] == 3
        names = {p["name"]: p for p in page["items"]}
        assert names[store["rice"]["name"]]["price"] == "50.00" and names[store["rice"]["name"]]["in_stock"] is True

    def test_nothing_private_is_ever_shown(self, anon, store):
        text = anon.get(f"{PUBLIC}/{SLUG}/products").text + anon.get(f"{PUBLIC}/{SLUG}").text
        for word in ("cost", "avg_cost", "stock", "shop_id", "sku", "margin", "supplier", "current_stock", "mrp"):
            assert f'"{word}"' not in text.replace('"in_stock"', ""), word

    def test_an_unknown_store_is_not_found_and_the_first_page_is_capped(self, anon, store):
        assert anon.get(f"{PUBLIC}/no-such-shop").status_code == 404
        assert anon.get(f"{PUBLIC}/{SLUG}/products", params={"limit": 1000}).status_code == 422

    def test_hidden_and_inactive_products_are_not_shown_and_out_of_stock_says_so(self, anon, client_a, store):
        client_a.put(f"{STORE}/listings/{store['sugar']['id']}", json={"visible": False})
        client_a.post(f"/api/v1/products/{store['oil']['id']}/deactivate")
        assert [p["name"] for p in anon.get(f"{PUBLIC}/{SLUG}/products").json()["items"]] == [store["rice"]["name"]]
        from tests.test_sales_api import sold

        sold(client_a, [{"product_id": store["rice"]["id"], "quantity": "10"}])
        assert anon.get(f"{PUBLIC}/{SLUG}/products").json()["items"][0]["in_stock"] is False

    def test_search_and_category_filter(self, anon, store):
        assert anon.get(f"{PUBLIC}/{SLUG}/products", params={"q": "rice"}).json()["total"] == 1
        assert anon.get(f"{PUBLIC}/{SLUG}/products", params={"q": "zzz"}).json()["total"] == 0


class TestPlacingAnOrder:
    def test_an_order_is_priced_by_the_server_and_moves_nothing(self, anon, client_a, store, session, tenant_a):
        before = sale_count(session, tenant_a.shop.id)
        o = place(anon, [(store["rice"], "3"), (store["sugar"], "2.5")])
        assert o["status"] == "PLACED" and o["total_amount"] == "270.00"  # 3 x 50 + 2.5 x 48
        assert o["order_no"].startswith("ORD/") and o["reference"] == o["order_no"].replace("/", "-") and o["tracking_token"]
        assert [i["line_total"] for i in o["items"]] == ["150.00", "120.00"]
        assert stock(client_a, store["rice"]) == 10 and sale_count(session, tenant_a.shop.id) == before  # nothing moved
        assert D(client_a.get("/api/v1/finance/pnl").json()["detailed_sales"]) == D("0")

    def test_the_customer_cannot_send_a_price_total_or_status(self, anon, store):
        for extra in ({"total_amount": "1"}, {"status": "DELIVERED"}, {"unit_price": "1"}, {"shop_id": 9}):
            r = anon.post(f"{PUBLIC}/{SLUG}/orders", json={**order_body([(store["rice"], "1")]), **extra}, headers={"Idempotency-Key": key()})
            assert r.status_code == 422, extra
        line = order_body([(store["rice"], "1")])
        line["items"][0]["unit_price"] = "1"
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=line, headers={"Idempotency-Key": key()}).status_code == 422

    def test_a_request_key_is_required(self, anon, store):
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]))
        assert r.status_code == 422
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": "short"})
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "change, expect",
        [
            ({"items": []}, 422),
            ({"customer_name": ""}, 422),
            ({"customer_phone": "abc"}, 422),
            ({"customer_phone": ""}, 422),
            ({"fulfilment": "TELEPORT"}, 422),
            ({"payment": "CARD"}, 422),
            ({"delivery_address": ""}, 422),
            ({"delivery_address": "x"}, 422),
            ({"notes": "n" * 400}, 422),
            ({"customer_name": "a" * 200}, 422),
        ],
    )
    def test_invalid_details_are_refused(self, anon, store, change, expect):
        body = order_body([(store["rice"], "1")])
        body.update(change)
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=body, headers={"Idempotency-Key": key()})
        assert r.status_code == expect, r.text

    def test_products_quantities_and_stock_are_checked(self, anon, client_a, store):
        for items, status in (
            ([(store["rice"], "0")], 422),
            ([(store["rice"], "1.5")], 422),  # pieces are whole
            ([(store["rice"], "11")], 422),  # only 10 in stock
            ([(store["rice"], "-1")], 422),
        ):
            assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body(items), headers={"Idempotency-Key": key()}).status_code == status
        client_a.put(f"{STORE}/listings/{store['rice']['id']}", json={"visible": False})
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": key()})
        assert r.status_code == 422 and "not available" in r.text
        ghost = {"id": 99999}
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(ghost, "1")]), headers={"Idempotency-Key": key()}).status_code == 422

    def test_the_same_product_twice_is_one_line_and_too_many_lines_are_refused(self, anon, store):
        o = place(anon, [(store["rice"], "1"), (store["rice"], "2")])
        assert len(o["items"]) == 1 and o["items"][0]["quantity"] == "3"
        body = order_body([(store["rice"], "1")])
        body["items"] = [{"product_id": i + 1, "quantity": "1"} for i in range(31)]
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=body, headers={"Idempotency-Key": key()}).status_code == 422

    def test_a_closed_store_and_unavailable_options_refuse(self, anon, client_a, store):
        client_a.put(f"{STORE}/settings", json={"delivery_enabled": False, "accepts_upi": False})
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": key()}).status_code == 422  # delivery
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")], fulfilment="PICKUP", payment="UPI"), headers={"Idempotency-Key": key()}).status_code == 422
        o = place(anon, [(store["rice"], "1")], fulfilment="PICKUP", delivery_address="ignored", payment="COD")
        assert o["fulfilment"] == "PICKUP"
        client_a.put(f"{STORE}/settings", json={"is_open": False})
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")], fulfilment="PICKUP"), headers={"Idempotency-Key": key()})
        assert r.status_code == 409 and r.json()["error_code"] == "store_closed"

    def test_the_minimum_order_is_enforced(self, anon, client_a, store):
        client_a.put(f"{STORE}/settings", json={"min_order_amount": "200"})
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": key()}).status_code == 422
        assert place(anon, [(store["rice"], "4")])["total_amount"] == "200.00"

    def test_a_repeated_request_returns_the_same_order_once(self, anon, session, tenant_a, store):
        k = key()
        one = place(anon, [(store["rice"], "1")], k=k)
        two = place(anon, [(store["rice"], "1")], k=k)
        assert one["order_no"] == two["order_no"] and one["tracking_token"] == two["tracking_token"]
        session.commit()
        assert session.scalar(select(func.count()).select_from(OnlineOrder).where(OnlineOrder.shop_id == tenant_a.shop.id)) == 1
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "2")]), headers={"Idempotency-Key": k})
        assert r.status_code == 409 and r.json()["error_code"] == "idempotency_key_reused"

    def test_simultaneous_identical_requests_make_one_order(self, real_client, session, tenant_a, store):
        k = key()
        clients = [real_client() for _ in range(5)]

        def go(c):
            return c.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": k})

        with ThreadPoolExecutor(5) as pool:
            results = list(pool.map(go, clients))
        assert {r.status_code for r in results} == {201} and len({r.json()["order_no"] for r in results}) == 1
        session.commit()
        assert session.scalar(select(func.count()).select_from(OnlineOrder).where(OnlineOrder.shop_id == tenant_a.shop.id)) == 1

    def test_one_phone_cannot_flood_the_shop_with_waiting_orders(self, anon, store):
        for _ in range(5):
            place(anon, [(store["rice"], "1")])
        r = anon.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")]), headers={"Idempotency-Key": key()})
        assert r.status_code == 409 and r.json()["error_code"] == "too_many_open_orders"
        place(anon, [(store["rice"], "1")], customer_phone="9876500002")  # someone else is unaffected

    def test_the_shop_is_told_and_the_order_is_on_record(self, anon, client_a, store, session, tenant_a):
        o = place(anon, [(store["rice"], "1")])
        detail = client_a.get(f"{ORDERS}/{order_id(client_a, o)}").json()
        assert [e["to_status"] for e in detail["events"]] == ["PLACED"] and detail["events"][0]["actor"] == "CUSTOMER"
        inbox = client_a.get("/api/v1/notifications").json()
        assert any("online order" in str(n).lower() for n in (inbox["items"] if isinstance(inbox, dict) else inbox))

    def test_placing_is_rate_limited_per_address(self, real_client, store, monkeypatch):
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_STORE_ORDER", "3")
        from app.core import ratelimit
        from app.core.config import get_settings

        get_settings.cache_clear()
        ratelimit.limiter.reset()
        c = real_client()
        codes = [c.post(f"{PUBLIC}/{SLUG}/orders", json=order_body([(store["rice"], "1")], customer_phone=f"98765000{i:02d}"), headers={"Idempotency-Key": key()}).status_code for i in range(5)]
        assert codes[:3] == [201, 201, 201] and 429 in codes[3:]
        ratelimit.limiter.reset()
        get_settings.cache_clear()


class TestTracking:
    def test_the_token_shows_the_order_and_nothing_personal(self, anon, store):
        o = place(anon, [(store["rice"], "2")])
        r = anon.get(f"{PUBLIC}/{SLUG}/orders/{o['reference']}", params={"token": o["tracking_token"]})
        assert r.status_code == 200 and r.json()["status"] == "PLACED" and r.json()["tracking_token"] is None
        assert r.json()["can_cancel"] is True and r.json()["timeline"][0]["status"] == "PLACED"
        for private in ("9876500001", "Asha", "MG Road", "customer", "shop_id"):
            assert private not in r.text

    def test_every_wrong_guess_is_the_same_not_found(self, anon, client_b, store):
        o = place(anon, [(store["rice"], "1")])
        good = f"{PUBLIC}/{SLUG}/orders/{o['reference']}"
        assert anon.get(good).status_code == 404
        assert anon.get(good, params={"token": "0" * 32}).status_code == 404
        assert anon.get(f"{PUBLIC}/{SLUG}/orders/ORD-2026-27-9999", params={"token": o["tracking_token"]}).status_code == 404
        assert anon.get(f"{PUBLIC}/{SLUG}/orders/not-a-number", params={"token": o["tracking_token"]}).status_code == 404
        open_store(client_b, slug="other-shop")
        assert anon.get(f"{PUBLIC}/other-shop/orders/{o['reference']}", params={"token": o["tracking_token"]}).status_code == 404

    def test_the_token_is_stored_only_as_a_hash(self, anon, store, session):
        o = place(anon, [(store["rice"], "1")])
        session.commit()
        row = session.scalars(select(OnlineOrder)).one()
        assert o["tracking_token"] not in (row.tracking_hash, row.idempotency_key) and len(row.tracking_hash) == 64

    def test_the_customer_can_cancel_only_before_the_shop_accepts(self, anon, client_a, store):
        o = place(anon, [(store["rice"], "1")])
        url = f"{PUBLIC}/{SLUG}/orders/{o['reference']}/cancel"
        assert anon.post(url, params={"token": "bad"}).status_code == 404
        assert anon.post(url, params={"token": o["tracking_token"]}).json()["status"] == "CANCELLED"
        assert anon.post(url, params={"token": o["tracking_token"]}).status_code == 409
        second = place(anon, [(store["rice"], "1")])
        client_a.post(f"{ORDERS}/{order_id(client_a, second)}/accept")
        r = anon.post(f"{PUBLIC}/{SLUG}/orders/{second['reference']}/cancel", params={"token": second["tracking_token"]})
        assert r.status_code == 409 and r.json()["error_code"] == "order_in_progress"


class TestShopWorkflow:
    def test_accepting_adds_the_customer_once_and_reuses_them_after(self, anon, client_a, store):
        first = place(anon, [(store["rice"], "1")])
        detail = client_a.post(f"{ORDERS}/{order_id(client_a, first)}/accept").json()
        assert detail["status"] == "ACCEPTED" and detail["customer_id"]
        customers = client_a.get("/api/v1/customers", params={"q": "9876500001"}).json()
        assert customers["total"] == 1
        second = place(anon, [(store["rice"], "1")])
        again = client_a.post(f"{ORDERS}/{order_id(client_a, second)}/accept").json()
        assert again["customer_id"] == detail["customer_id"]
        assert client_a.get("/api/v1/customers", params={"q": "9876500001"}).json()["total"] == 1

    def test_the_order_states_only_move_along_the_allowed_paths(self, anon, client_a, store):
        o = place(anon, [(store["rice"], "1")])
        oid = order_id(client_a, o)
        adv = lambda s: client_a.post(f"{ORDERS}/{oid}/advance", json={"status": s})  # noqa: E731
        assert adv("PREPARING").status_code == 409  # not accepted yet
        assert client_a.post(f"{ORDERS}/{oid}/accept").status_code == 200
        assert client_a.post(f"{ORDERS}/{oid}/accept").status_code == 409  # not twice
        assert client_a.post(f"{ORDERS}/{oid}/reject", json={"reason": "no"}).status_code in (409, 422)
        assert adv("DELIVERED").status_code == 409  # not straight from accepted
        assert adv("PLACED").status_code == 422 and adv("ACCEPTED").status_code == 422
        assert adv("PREPARING").json()["status"] == "PREPARING"
        assert adv("READY").json()["status"] == "READY"
        assert adv("DELIVERED").status_code == 409  # a delivery goes out first
        assert adv("OUT_FOR_DELIVERY").json()["status"] == "OUT_FOR_DELIVERY"

    def test_a_pickup_order_is_not_sent_out(self, anon, client_a, store):
        o = place(anon, [(store["rice"], "1")], fulfilment="PICKUP")
        oid = order_id(client_a, o)
        client_a.post(f"{ORDERS}/{oid}/accept")
        client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"})
        assert client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "OUT_FOR_DELIVERY"}).status_code == 409
        assert client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).status_code == 200

    def test_rejecting_and_cancelling_need_a_reason_and_create_no_sale(self, anon, client_a, store, session, tenant_a):
        o1, o2 = place(anon, [(store["rice"], "1")]), place(anon, [(store["rice"], "1")])
        a, b = order_id(client_a, o1), order_id(client_a, o2)
        assert client_a.post(f"{ORDERS}/{a}/reject", json={"reason": ""}).status_code == 422
        assert client_a.post(f"{ORDERS}/{a}/reject", json={}).status_code == 422
        r = client_a.post(f"{ORDERS}/{a}/reject", json={"reason": "Out of stock today"}).json()
        assert r["status"] == "REJECTED" and r["decision_reason"] == "Out of stock today" and r["customer_id"] is None
        client_a.post(f"{ORDERS}/{b}/accept")
        assert client_a.post(f"{ORDERS}/{b}/cancel", json={"reason": "Customer called"}).json()["status"] == "CANCELLED"
        assert client_a.post(f"{ORDERS}/{a}/cancel", json={"reason": "again"}).status_code == 409
        assert sale_count(session, tenant_a.shop.id) == 0 and stock(client_a, store["rice"]) == 10

    def test_delivery_creates_exactly_one_ordinary_posted_sale(self, anon, client_a, store, session, tenant_a):
        o = place(anon, [(store["rice"], "3"), (store["sugar"], "2.5")], payment="UPI")
        oid = order_id(client_a, o)
        for step in ("accept",):
            client_a.post(f"{ORDERS}/{oid}/{step}")
        for s in ("PREPARING", "READY", "OUT_FOR_DELIVERY"):
            client_a.post(f"{ORDERS}/{oid}/advance", json={"status": s})
        assert stock(client_a, store["rice"]) == 10  # still nothing taken
        done = client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"})
        assert done.status_code == 200, done.text
        d = done.json()
        assert d["status"] == "DELIVERED" and d["sale_id"] and d["invoice_no"] and d["sale_total"] == "270.00"
        sale = client_a.get(f"/api/v1/sales/{d['sale_id']}").json()
        assert sale["status"] == "POSTED" and sale["payment_method"] == "UPI" and sale["customer_id"] == d["customer_id"]
        assert sale["total_amount"] == "270.00" and sale["amount_paid"] == "270.00"
        assert stock(client_a, store["rice"]) == 7 and stock(client_a, store["sugar"]) == D("147.5")
        assert sale_count(session, tenant_a.shop.id) == 1
        assert owed(client_a, {"id": d["customer_id"]}) == D("0.00")  # paid at the door: no credit
        assert client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).status_code == 409
        assert client_a.post(f"{ORDERS}/{oid}/cancel", json={"reason": "too late"}).status_code == 409
        assert sale_count(session, tenant_a.shop.id) == 1  # never a second sale
        pnl = client_a.get("/api/v1/finance/pnl").json()
        assert D(pnl["detailed_sales"]) == D("270.00")  # it is an ordinary sale in every report

    def test_cash_on_delivery_posts_a_cash_sale(self, anon, client_a, store):
        o = place(anon, [(store["rice"], "1")], fulfilment="PICKUP")
        oid = order_id(client_a, o)
        client_a.post(f"{ORDERS}/{oid}/accept")
        client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"})
        d = client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).json()
        assert client_a.get(f"/api/v1/sales/{d['sale_id']}").json()["payment_method"] == "CASH"

    def test_delivery_is_refused_and_changes_nothing_when_the_stock_has_gone(self, anon, client_a, store, session, tenant_a):
        o = place(anon, [(store["rice"], "6")], fulfilment="PICKUP")
        oid = order_id(client_a, o)
        client_a.post(f"{ORDERS}/{oid}/accept")
        client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"})
        from tests.test_sales_api import sold

        sold(client_a, [{"product_id": store["rice"]["id"], "quantity": "8"}])  # the shelf empties at the counter
        detail = client_a.get(f"{ORDERS}/{oid}").json()
        assert detail["warnings"] and "rice" in detail["warnings"][0].lower()
        r = client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"})
        assert r.status_code == 409, r.text
        after = client_a.get(f"{ORDERS}/{oid}").json()
        assert after["status"] == "READY" and after["sale_id"] is None and [e["to_status"] for e in after["events"]][-1] == "READY"
        assert sale_count(session, tenant_a.shop.id) == 1 and stock(client_a, store["rice"]) == 2
        client_a.post(f"{ORDERS}/{oid}/cancel", json={"reason": "Out of stock"})  # the shop's way out

    def test_a_delivered_order_can_be_returned_like_any_sale(self, anon, client_a, store):
        o = place(anon, [(store["rice"], "2")], fulfilment="PICKUP")
        oid = order_id(client_a, o)
        client_a.post(f"{ORDERS}/{oid}/accept")
        client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"})
        d = client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).json()
        sale = client_a.get(f"/api/v1/sales/{d['sale_id']}").json()
        returned(client_a, sale, [back(sale, 0, "1")])
        assert stock(client_a, store["rice"]) == 9

    def test_an_existing_customer_by_phone_is_used_and_a_deactivated_one_is_not(self, anon, client_a, store):
        from tests.test_sales_api import make_customer

        known = make_customer(client_a, phone="9876500001")
        first = place(anon, [(store["rice"], "1")])
        assert client_a.post(f"{ORDERS}/{order_id(client_a, first)}/accept").json()["customer_id"] == known["id"]
        client_a.post(f"/api/v1/customers/{known['id']}/deactivate")
        second = place(anon, [(store["rice"], "1")])
        assert client_a.post(f"{ORDERS}/{order_id(client_a, second)}/accept").json()["customer_id"] is None

    def test_the_lists_summary_and_filters(self, anon, client_a, store):
        a, b = place(anon, [(store["rice"], "1")]), place(anon, [(store["rice"], "1")], customer_phone="9876500002", customer_name="Bina")
        client_a.post(f"{ORDERS}/{order_id(client_a, a)}/accept")
        assert client_a.get(ORDERS).json()["total"] == 2
        assert client_a.get(ORDERS, params={"status": "ACCEPTED"}).json()["total"] == 1
        assert client_a.get(ORDERS, params={"q": "Bina"}).json()["items"][0]["order_no"] == b["order_no"]
        assert client_a.get(ORDERS, params={"status": "NOPE"}).status_code == 422
        s = client_a.get(f"{ORDERS}/summary").json()
        assert s["by_status"]["PLACED"] == 1 and s["by_status"]["ACCEPTED"] == 1 and s["open"] == 2

    def test_every_change_leaves_history_that_cannot_be_edited(self, anon, client_a, store, session):
        from sqlalchemy import text

        o = place(anon, [(store["rice"], "1")])
        client_a.post(f"{ORDERS}/{order_id(client_a, o)}/accept")
        session.commit()
        assert session.scalar(select(func.count()).select_from(OnlineOrderEvent)) == 2
        with pytest.raises(Exception, match="insert-only"):
            session.execute(text("UPDATE online_order_events SET note = 'x'"))
        session.rollback()
        with pytest.raises(Exception, match="insert-only"):
            session.execute(text("DELETE FROM online_order_events"))
        session.rollback()


class TestIsolationAndAccess:
    def test_shops_never_see_or_touch_each_others_orders(self, anon, client_a, client_b, store, tenant_b):
        o = place(anon, [(store["rice"], "1")])
        oid = order_id(client_a, o)
        assert client_b.get(ORDERS).json()["total"] == 0
        assert client_b.get(f"{ORDERS}/{oid}").status_code == 404
        for path, body in (("accept", None), ("reject", {"reason": "abc"}), ("cancel", {"reason": "abc"}), ("advance", {"status": "PREPARING"})):
            assert client_b.post(f"{ORDERS}/{oid}/{path}", json=body).status_code == 404, path
        assert client_a.get(f"{ORDERS}/{oid}").json()["status"] == "PLACED"

    def test_a_shops_store_lists_only_its_own_products(self, anon, client_a, client_b, store, tenant_b, units):
        from tests.test_purchases_api import make_product

        open_store(client_b, slug="second-shop")
        theirs = make_product(client_b, tenant_b, units, "THEIRS", selling_price="10")
        list_products(client_b, theirs)
        names = [p["name"] for p in anon.get(f"{PUBLIC}/second-shop/products").json()["items"]]
        assert names == [theirs["name"]] and store["rice"]["name"] not in names
        # ordering another shop's product through this shop's address is refused
        body = order_body([(theirs, "1")])
        assert anon.post(f"{PUBLIC}/{SLUG}/orders", json=body, headers={"Idempotency-Key": key()}).status_code == 422

    def test_staff_permissions(self, anon, make_client, tenant_a, client_a, store):
        o = place(anon, [(store["rice"], "1")], fulfilment="PICKUP")
        oid = order_id(client_a, o)
        nothing = client_with(make_client, tenant_a, {"PRODUCT_VIEW"})
        assert nothing.get(ORDERS).status_code == 403 and nothing.get(f"{STORE}/settings").status_code == 403
        viewer = client_with(make_client, tenant_a, {"ONLINE_ORDER_VIEW"})
        assert viewer.get(ORDERS).status_code == 200 and viewer.post(f"{ORDERS}/{oid}/accept").status_code == 403
        assert viewer.put(f"{STORE}/settings", json={"is_open": False}).status_code == 403
        accepter = client_with(make_client, tenant_a, {"ONLINE_ORDER_VIEW", "ONLINE_ORDER_ACCEPT"})
        assert accepter.post(f"{ORDERS}/{oid}/accept").status_code == 200
        assert accepter.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"}).status_code == 403
        mover = client_with(make_client, tenant_a, {"ONLINE_ORDER_VIEW", "ONLINE_ORDER_STATUS_UPDATE"})
        assert mover.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"}).status_code == 200
        # delivering also sells: it needs the permission to sell as well
        assert mover.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).status_code == 403
        seller = client_with(make_client, tenant_a, {"ONLINE_ORDER_VIEW", "ONLINE_ORDER_STATUS_UPDATE", "SALE_CREATE", "SALE_POST"})
        assert seller.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).status_code == 200
        manager = client_with(make_client, tenant_a, {"STORE_SETTINGS_MANAGE"})
        assert manager.put(f"{STORE}/settings", json={"announcement": "Diwali sale"}).status_code == 200

    def test_the_staff_routes_need_a_sign_in_and_the_public_ones_do_not(self, anon, store):
        assert anon.get(ORDERS).status_code == 401 and anon.get(f"{STORE}/settings").status_code == 401
        assert anon.get(f"{PUBLIC}/{SLUG}").status_code == 200

    def test_there_is_no_delete_route(self, client_a):
        paths = client_a.app.openapi()["paths"]
        assert not any("delete" in v for p, v in paths.items() if "online-orders" in p or "/store" in p or "/public/" in p)

    def test_the_store_permission_is_the_existing_settings_one_held_by_owner_and_manager(self):
        from app.core.permissions import SYSTEM_ROLES

        holders = {code for code, (_, _, perms) in SYSTEM_ROLES.items() if "STORE_SETTINGS_MANAGE" in perms}
        assert holders == {"OWNER", "MANAGER"}


class TestReportsSeeOnlineOrders:
    """Nothing says 'no online orders' any more: the numbers are the shop's own, and delivered orders are never counted twice."""

    def _deliver_one_and_leave_one(self, anon, client_a, store):
        a = place(anon, [(store["rice"], "2")], fulfilment="PICKUP")
        place(anon, [(store["rice"], "1")], customer_phone="9876500002", customer_name="Bina")
        oid = order_id(client_a, a)
        client_a.post(f"{ORDERS}/{oid}/accept")
        client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "READY"})
        return client_a.post(f"{ORDERS}/{oid}/advance", json={"status": "DELIVERED"}).json()

    def test_the_kpi_the_insights_and_the_customer_report_use_the_real_orders(self, anon, client_a, store):
        delivered = self._deliver_one_and_leave_one(anon, client_a, store)
        dash = client_a.get("/api/v1/analytics/executive", params={"preset": "this_month"}).json()
        section = next(s for s in dash["sections"] if s["key"] == "online_orders")
        kpi = next(k for k in section["kpis"] if k["definition"]["key"] == "online_orders")
        assert kpi["current"]["availability"] == "AVAILABLE" and D(kpi["current"]["amount"]) == 2
        insights = client_a.get("/api/v1/analytics/overview").json()
        assert insights["online_store"]["available"] is True and insights["online_store"]["placed"] == 2
        assert insights["online_store"]["delivered"] == 1 and insights["online_store"]["delivered_value"] == delivered["sale_total"]
        customers = client_a.get("/api/v1/analytics/customers/overview", params={"preset": "this_month"}).json()
        assert customers["online_customer_activity"]["delivered"] == 1
        assert "already inside" in customers["online_note"]

    def test_delivered_revenue_is_in_the_sales_figures_exactly_once(self, anon, client_a, store):
        delivered = self._deliver_one_and_leave_one(anon, client_a, store)
        pnl = client_a.get("/api/v1/finance/pnl").json()
        assert pnl["detailed_sales"] == delivered["sale_total"] and D(pnl["online_sales"]) == 0  # inside detailed; no second channel

    def test_the_customer_profile_counts_accepted_orders(self, anon, client_a, store):
        delivered = self._deliver_one_and_leave_one(anon, client_a, store)
        rows = client_a.get("/api/v1/crm/segments").json()["items"]
        me = next(r for r in rows if r["customer_id"] == delivered["customer_id"])
        assert me["online_order_count"] == 1
        assert client_a.get(f"/api/v1/crm/customers/{delivered['customer_id']}/profile").json()["analytics"]["online_order_count"] == 1

    def test_the_assistant_answers_from_the_real_orders(self, anon, client_a, store, session, tenant_a):
        self._deliver_one_and_leave_one(anon, client_a, store)
        from app.services import ai_tools
        from tests.test_phase16_ai import _tc

        session.commit()  # end the stale read snapshot
        tool = ai_tools.TOOLS["get_online_order_summary"]
        a = tool.run(_tc(session, tenant_a), tool.args.model_validate({"period": "this month"}))
        assert a.status == "ANSWERED" and {f.label: f.value for f in a.figures}["Orders placed"] == "2"
