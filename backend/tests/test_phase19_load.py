# ruff: noqa: F811
"""Load tests: realistic concurrency against a shop with a real history. They look for actual bottlenecks and for correctness under load
(no lost stock, no double sales, no deadlocks), not for microseconds. Run with `-s` to print the measured numbers.

SQLite (development) serialises writers: these tests show how the application behaves on that floor. PostgreSQL, the production target, allows
concurrent writers and is expected to do better; docs/PERFORMANCE.md says what has and has not been measured there."""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from tests.test_purchases_api import stock
from tests.test_sales_api import shelf  # noqa: F401

API = "/api/v1"


def _run(clients, fn):
    barrier = Barrier(len(clients))

    def work(pair):
        i, client = pair
        barrier.wait()
        start = time.perf_counter()
        r = fn(client, i)
        return r, time.perf_counter() - start

    with ThreadPoolExecutor(len(clients)) as pool:
        return list(pool.map(work, list(enumerate(clients))))


def _report(name, results):
    times = sorted(t for _, t in results)
    print(
        f"LOAD {name}: n={len(times)} median={times[len(times) // 2] * 1000:.0f}ms p95={times[int(len(times) * 0.95) - 1] * 1000:.0f}ms max={times[-1] * 1000:.0f}ms"
    )  # noqa: T201


def test_concurrent_logins(sign_in, owner_login, real_client):
    def login(_c, i):
        c = real_client()
        return c.post(
            f"{API}/auth/login", json={"email": owner_login, "password": "correct horse battery staple"}
        ).status_code

    results = _run([None] * 12, login)
    _report("12 simultaneous sign-ins", results)
    assert all(code == 200 for code, _ in results)
    assert max(t for _, t in results) < 15


def test_concurrent_sales_of_different_products_all_succeed_and_stock_is_exact(
    client_a, make_client, tenant_a, shelf
):  # noqa: F811
    rice = shelf["rice"]  # 10 in stock
    clients = [make_client(tenant_a) for _ in range(10)]

    def sell(c, i):
        d = c.post(f"{API}/sales", json={"items": [{"product_id": rice["id"], "quantity": "1"}]})
        assert d.status_code == 201, d.text
        return c.post(f"{API}/sales/{d.json()['id']}/post", json={"payment_method": "CASH"}).status_code

    results = _run(clients, sell)
    _report("10 simultaneous sales of the same product", results)
    codes = sorted(code for code, _ in results)
    assert (
        codes == [200] * 10 and stock(client_a, rice) == 0
    )  # exactly the stock there was: none oversold, none lost


def test_more_buyers_than_stock_exactly_the_stock_is_sold(client_a, make_client, tenant_a, shelf):  # noqa: F811
    rice = shelf["rice"]
    clients = [make_client(tenant_a) for _ in range(14)]

    def sell(c, i):
        d = c.post(f"{API}/sales", json={"items": [{"product_id": rice["id"], "quantity": "1"}]}).json()
        return c.post(f"{API}/sales/{d['id']}/post", json={"payment_method": "CASH"}).status_code

    results = _run(clients, sell)
    _report("14 buyers for 10 units", results)
    codes = [code for code, _ in results]
    assert codes.count(200) == 10 and codes.count(409) == 4 and stock(client_a, rice) == 0


def test_reads_stay_quick_while_writes_are_happening(client_a, make_client, tenant_a, shelf):  # noqa: F811
    rice = shelf["rice"]
    writers = [make_client(tenant_a) for _ in range(4)]
    readers = [make_client(tenant_a) for _ in range(6)]

    def write(c, i):
        d = c.post(f"{API}/sales", json={"items": [{"product_id": rice["id"], "quantity": "1"}]}).json()
        return c.post(f"{API}/sales/{d['id']}/post", json={"payment_method": "CASH"}).status_code

    def read(c, i):
        codes = [
            c.get(f"{API}/inventory").status_code,
            c.get(f"{API}/analytics/sales/summary", params={"preset": "this_month"}).status_code,
            c.get(f"{API}/sales").status_code,
        ]
        return codes

    with ThreadPoolExecutor(10) as pool:
        barrier = Barrier(10)

        def wrap(args):
            fn, c, i = args
            barrier.wait()
            start = time.perf_counter()
            return fn(c, i), time.perf_counter() - start

        jobs = [(write, c, i) for i, c in enumerate(writers)] + [(read, c, i) for i, c in enumerate(readers)]
        results = list(pool.map(wrap, jobs))
    _report("4 sales + 6 dashboards at once", results)
    assert all(r == 200 for r, _ in results[:4]) and all(r == [200, 200, 200] for r, _ in results[4:])
    assert max(t for _, t in results) < 15


def test_concurrent_khata_payments_all_count(client_a, make_client, tenant_a):
    c = client_a.post(f"{API}/customers", json={"name": "Asha", "opening_balance": "1000"}).json()["customer"]
    clients = [make_client(tenant_a) for _ in range(8)]
    results = _run(
        clients,
        lambda cl, i: (
            cl.post(
                f"{API}/customers/{c['id']}/payments", json={"amount": "10", "payment_method": "CASH"}
            ).status_code
        ),
    )
    _report("8 simultaneous khata payments", results)
    assert [code for code, _ in results] == [201] * 8
    assert Decimal(client_a.get(f"{API}/customers/{c['id']}/balance").json()["balance"]) == Decimal("920.00")


def test_offline_sync_batches_from_several_devices_at_once(client_a, make_client, tenant_a, shelf):  # noqa: F811
    rice = shelf["rice"]
    clients = [make_client(tenant_a) for _ in range(5)]

    def batch(c, i):
        ops = [
            {
                "client_op_id": f"load-{i}-{n:03d}-xxxxxxxx",
                "type": "QUICK_SALE",
                "payload": {"create": {"gross_amount": "10.00"}, "payment": {"payment_method": "CASH"}},
            }
            for n in range(5)
        ]
        return [
            r["status"]
            for r in c.post(f"{API}/sync/operations", json={"device_id": f"d{i}", "operations": ops}).json()[
                "results"
            ]
        ]

    results = _run(clients, batch)
    _report("5 devices x 5 offline operations", results)
    assert all(r == ["SYNCED"] * 5 for r, _ in results)
    assert stock(client_a, rice) == 10  # quick sales never touch stock
    again = _run(clients, batch)
    assert all(
        r == ["SYNCED"] * 5 for r, _ in again
    )  # a full replay of every batch: all recognised, nothing doubled
    assert len(client_a.get(f"{API}/quick-sales", params={"limit": 200}).json()["items"]) == 25


@pytest.mark.parametrize("rounds", [1])
def test_a_large_export_does_not_block_selling(client_a, make_client, tenant_a, shelf, rounds):  # noqa: F811
    rice = shelf["rice"]
    exporter, seller = make_client(tenant_a), make_client(tenant_a)

    def export(c, i):
        return c.get(f"{API}/exports/products").status_code

    def sell(c, i):
        d = c.post(f"{API}/sales", json={"items": [{"product_id": rice["id"], "quantity": "1"}]}).json()
        return c.post(f"{API}/sales/{d['id']}/post", json={"payment_method": "CASH"}).status_code

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(export, exporter, 0)
        b = pool.submit(sell, seller, 0)
        assert (a.result(), b.result()) == (200, 200)


def test_password_hashing_never_runs_more_than_the_configured_number_at_once(monkeypatch):
    import threading

    from app.core.config import get_settings
    from app.services import password_service

    monkeypatch.setenv("KIRANA_PASSWORD_HASH_CONCURRENCY", "2")
    get_settings.cache_clear()
    password_service._gate = None  # noqa: SLF001
    live, peak, lock = [0], [0], threading.Lock()

    class Slow:
        def hash(self, password):  # noqa: ANN001, ANN201
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return "h"

    monkeypatch.setattr(password_service, "_current", lambda settings=None: Slow())
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: password_service.hash_password("x"), range(8)))
    assert peak[0] == 2


def test_the_server_database_pool_is_bounded_and_configurable(monkeypatch):
    """The PostgreSQL pool settings reach the engine (a live PostgreSQL server is not available here, so the engine is only built)."""
    from sqlalchemy.pool import QueuePool

    from app.db import engine as engine_module

    made = {}
    monkeypatch.setattr(engine_module, "create_engine", lambda url, **kw: made.update(kw) or object())
    engine_module.create_db_engine(
        "postgresql://u:p@localhost/db",
        pool={"pool_size": 3, "max_overflow": 1, "pool_recycle": 60, "pool_timeout": 5},
    )
    assert made == {
        "echo": False,
        "pool_pre_ping": True,
        "pool_size": 3,
        "max_overflow": 1,
        "pool_recycle": 60,
        "pool_timeout": 5,
    }
    assert QueuePool  # the default server pool type is a bounded queue
