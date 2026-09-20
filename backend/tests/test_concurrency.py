"""Simultaneous requests. SQLite runs writers one at a time (BEGIN IMMEDIATE), so a check followed by an insert
can never interleave with another request's. These tests fire real requests at the same moment."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.models import InventoryTransaction, Product

PRODUCTS = "/api/v1/products"


def race(clients, request):
    """Run `request(client)` on every client at the same instant; return the sorted status codes."""
    barrier = threading.Barrier(len(clients))

    def run(client):
        barrier.wait()
        return request(client).status_code

    with ThreadPoolExecutor(len(clients)) as pool:
        return sorted(pool.map(run, clients))


@pytest.fixture
def body(tenant_a, units):
    return {
        "sku": "RACE",
        "name": "Race",
        "category_id": tenant_a.category.id,
        "unit_id": units["pcs"],
        "selling_price": "10",
    }


@pytest.mark.parametrize("attempt", range(5))
def test_two_simultaneous_creates_of_one_sku_make_exactly_one_product(
    make_client, tenant_a, body, fresh, attempt
):
    clients = [make_client(tenant_a) for _ in range(2)]
    payload = {**body, "sku": f"RACE-{attempt}"}

    assert race(clients, lambda c: c.post(PRODUCTS, json=payload)) == [201, 409]
    assert (
        fresh(
            lambda s: s.scalar(select(func.count()).select_from(Product).where(Product.sku == payload["sku"]))
        )
        == 1
    )


def test_two_simultaneous_creates_of_one_barcode_make_exactly_one_product(make_client, tenant_a, body, fresh):
    clients = [make_client(tenant_a) for _ in range(2)]
    payloads = iter(
        [{**body, "sku": "B1", "barcode": "8900000000123"}, {**body, "sku": "B2", "barcode": "8900000000123"}]
    )
    lock = threading.Lock()

    def request(client):
        with lock:
            payload = next(payloads)
        return client.post(PRODUCTS, json=payload)

    assert race(clients, request) == [201, 409]
    assert fresh(lambda s: s.scalar(select(func.count()).select_from(Product))) == 1


@pytest.mark.parametrize("attempt", range(3))
def test_a_double_tapped_opening_stock_is_recorded_once(make_client, tenant_a, body, fresh, attempt):
    client = make_client(tenant_a)
    product_id = client.post(PRODUCTS, json={**body, "sku": f"DT-{attempt}"}).json()["product"]["id"]
    clients = [make_client(tenant_a) for _ in range(3)]

    codes = race(
        clients,
        lambda c: c.post(
            "/api/v1/inventory/opening-stock", json={"product_id": product_id, "quantity": "10"}
        ),
    )

    assert codes == [201, 409, 409]
    rows = fresh(
        lambda s: s.scalars(
            select(InventoryTransaction).where(InventoryTransaction.product_id == product_id)
        ).all()
    )
    assert len(rows) == 1 and str(rows[0].qty_delta) == "10.000"  # stock is 10, never 20 or 30


def test_simultaneous_writes_by_different_shops_do_not_interfere(
    make_client, tenant_a, tenant_b, body, units, fresh
):
    clients = [make_client(tenant_a), make_client(tenant_b)]
    payloads = {id(clients[0]): body, id(clients[1]): {**body, "category_id": tenant_b.category.id}}

    codes = race(clients, lambda c: c.post(PRODUCTS, json=payloads[id(c)]))

    assert codes == [201, 201]  # the same SKU is fine in two shops, even at the same instant
    assert fresh(lambda s: s.scalar(select(func.count()).select_from(Product))) == 2
