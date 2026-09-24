"""Phase 19 baseline: the main endpoints over a large shop (25,000 sales, 3,000 products, 1,500 customers, 100,000 audit rows), measured over HTTP.

Run with `-s` to print the numbers recorded in docs/PERFORMANCE.md. The limits are generous: they catch a scan or an N+1, not milliseconds."""

import resource
import time

import pytest
from sqlalchemy import event, text

from tests.test_phase16_performance import CUSTOMERS, PRODUCTS, QUICK, SALES, big  # noqa: F401

API = "/api/v1"
AUDIT_ROWS = 100_000
NOW = "'2026-09-01 10:00:00.000000'"


@pytest.fixture
def huge(big, engine, tenant_a):  # noqa: F811
    shop, user = tenant_a.shop.id, tenant_a.user.id
    with engine.begin() as c:
        from sqlalchemy import inspect

        assert {"shop_id", "entity_type", "action"} <= {col["name"] for col in inspect(c).get_columns("audit_log")}
        c.execute(
            text(
                "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < :rows) "
                f"INSERT INTO audit_log (shop_id, user_id, entity_type, entity_id, action, created_at) SELECT :s, :u, 'sale', i, 'sale_posted', {NOW} FROM n"
            ),
            {"rows": AUDIT_ROWS, "s": shop, "u": user},
        )
    return big


TIMINGS: dict[str, tuple[float, int]] = {}


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)  # bytes on macOS


class TestApiLatency:
    def test_main_endpoints_over_http(self, huge, client_a, engine):
        counter = {"n": 0}

        @event.listens_for(engine, "before_cursor_execute")
        def _count(*_a):  # noqa: ANN002
            counter["n"] += 1

        checks = [
            ("products page", f"{API}/products", {"limit": 50}, 2.0, 30),
            ("products search", f"{API}/products", {"q": "Product 0123"}, 2.0, 30),
            ("customers page", f"{API}/customers", {"limit": 50}, 2.0, 40),
            ("customers search", f"{API}/customers", {"q": "Customer 0042"}, 2.0, 40),
            ("inventory page", f"{API}/inventory", {"limit": 50}, 3.0, 30),
            ("sales page", f"{API}/sales", {"limit": 50}, 2.0, 40),
            ("sales date filter", f"{API}/sales", {"date_from": "2025-06-01", "date_to": "2025-06-30", "limit": 50}, 2.0, 40),
            ("quick sales page", f"{API}/quick-sales", {"limit": 50}, 2.0, 40),
            ("purchases page", f"{API}/purchases", {"limit": 50}, 2.0, 40),
            ("dashboard overview", f"{API}/analytics/overview", {"date_from": "2025-09-25", "date_to": "2026-09-24", "bucket": "month"}, 8.0, 80),
            ("KPIs (year)", f"{API}/analytics/kpis", {"preset": "last_year"}, 8.0, 400),
            ("executive dashboard", f"{API}/analytics/executive", {"preset": "last_year"}, 8.0, 400),
            ("sales trend (month)", f"{API}/analytics/sales/trend", {"preset": "last_year", "bucket": "month"}, 5.0, 30),
            ("finance dashboard", f"{API}/finance/dashboard", {"date_from": "2026-01-01", "date_to": "2026-09-24"}, 8.0, 800),
            ("notifications", f"{API}/notifications", {}, 2.0, 30),
            ("audit-based screens", f"{API}/staff", {}, 2.0, 30),
            ("export sales", f"{API}/exports/sales", {}, 15.0, 60),
            ("export products", f"{API}/exports/products", {}, 10.0, 30),
            ("analytics export pdf", f"{API}/analytics/export/sales-products-top", {"format": "pdf", "preset": "last_year"}, 15.0, 60),
            ("sync snapshot products", f"{API}/sync/snapshot/products", {}, 5.0, 30),
        ]
        for name, path, params, limit, max_queries in checks:
            counter["n"] = 0
            start = time.perf_counter()
            r = client_a.get(path, params=params)
            took = time.perf_counter() - start
            TIMINGS[name] = (took, counter["n"])
            print(f"PERF {name}: {took * 1000:.0f} ms, {counter['n']} queries, {len(r.content) // 1024} KB, HTTP {r.status_code}")  # noqa: T201
            assert r.status_code == 200, (name, r.text[:200])
            assert took < limit, f"{name}: {took:.2f}s (limit {limit}s)"
            assert counter["n"] <= max_queries, f"{name}: {counter['n']} queries (limit {max_queries})"
        print(f"PERF peak memory of the test process: {_rss_mb():.0f} MB")  # noqa: T201
