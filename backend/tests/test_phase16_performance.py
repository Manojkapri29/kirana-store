"""Analytics performance smoke test: thousands of products, sales and ledger rows; every analytics report must stay quick and use
a bounded number of queries (no query per product or per customer). Not a benchmark: the limits are generous and catch a scan
or an N+1, not milliseconds. Run with `-s` to print the measured times (recorded in docs/ANALYTICS.md)."""

import time
from datetime import date

import pytest
from sqlalchemy import event, text

from app.reporting import (
    builder,
    catalog,
    cohorts,
    crosslinks,
    customers,
    executive,
    inventory,
    kpis,
    sales,
    suppliers,
)
from app.reporting import finance as finance_reports
from app.reporting.filters import CompareMode, Preset, build_filters
from tests.factories import today_in_shop_timezone

PRODUCTS, CUSTOMERS, SALES, QUICK, PURCHASES = 3000, 1500, 20000, 5000, 600
NOW = "'2026-09-01 10:00:00.000000'"
TODAY = today_in_shop_timezone()
TIMINGS: dict[str, tuple[float, int]] = {}


@pytest.fixture
def big(tenant_a, units, engine):
    shop, user, category, unit = tenant_a.shop.id, tenant_a.user.id, tenant_a.category.id, units["pcs"]
    year = TODAY.year
    with engine.begin() as c:
        c.execute(
            text(
                f"INSERT INTO products (shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, updated_at, created_at) VALUES (:s, :sku, :name, :cat, :unit, 5000, 2500, TRUE, {NOW}, {NOW})"
            ),
            [
                {"s": shop, "sku": f"P{i:05d}", "name": f"Product {i:05d}", "cat": category, "unit": unit}
                for i in range(PRODUCTS)
            ],
        )
        c.execute(
            text(
                f"INSERT INTO inventory_transactions (shop_id, product_id, txn_type, qty_delta, txn_date, created_by, created_at) SELECT :s, id, 'OPENING', 500000, :d, :u, {NOW} FROM products WHERE shop_id = :s"
            ),
            {"s": shop, "u": user, "d": f"{year - 1}-01-01"},
        )
        c.execute(
            text(
                f"INSERT INTO customers (shop_id, name, is_active, updated_at, created_at) VALUES (:s, :name, TRUE, {NOW}, {NOW})"
            ),
            [{"s": shop, "name": f"Customer {i:05d}"} for i in range(CUSTOMERS)],
        )
        first_customer = c.execute(
            text("SELECT MIN(id) FROM customers WHERE shop_id = :s"), {"s": shop}
        ).scalar()
        first_product = c.execute(
            text("SELECT MIN(id) FROM products WHERE shop_id = :s"), {"s": shop}
        ).scalar()
        c.execute(
            text(
                f"INSERT INTO sales (shop_id, invoice_no, status, sale_date, customer_id, subtotal, discount, promotion_discount, total_amount, payment_type, amount_paid, payment_method, created_by, posted_at, posted_by, updated_at, created_at) VALUES (:s, :no, 'POSTED', :d, :cust, 10000, 0, 0, 10000, 'PAID', 10000, 'CASH', :u, {NOW}, :u, {NOW}, {NOW})"
            ),
            [
                {
                    "s": shop,
                    "no": f"INV/{i}",
                    "d": date.fromordinal(date(year - 1, 1, 1).toordinal() + (i % 600)).isoformat(),
                    "cust": first_customer + (i % CUSTOMERS) if i % 3 else None,
                    "u": user,
                }
                for i in range(SALES)
            ],
        )
        c.execute(
            text(
                f"INSERT INTO sale_items (shop_id, sale_id, product_id, unit_id, quantity, unit_price, discount, line_total, promotion_discount, unit_cost, cogs_amount, updated_at, created_at) SELECT shop_id, id, :fp + (id % {PRODUCTS}), :unit, 1000, 10000, 0, 10000, 0, 6000, 6000, {NOW}, {NOW} FROM sales WHERE shop_id = :s"
            ),
            {"s": shop, "fp": first_product, "unit": unit},
        )
        c.execute(
            text(
                f"INSERT INTO inventory_transactions (shop_id, product_id, txn_type, qty_delta, unit_cost, txn_date, reference_type, reference_id, created_by, created_at) SELECT si.shop_id, si.product_id, 'SALE', -1000, 6000, s.sale_date, 'SALE_ITEM', si.id, :u, {NOW} FROM sale_items si JOIN sales s ON s.id = si.sale_id WHERE si.shop_id = :s"
            ),
            {"s": shop, "u": user},
        )
        c.execute(
            text(
                f"INSERT INTO quick_sales (shop_id, quick_no, status, sale_date, gross_amount, discount, total_amount, payment_type, amount_paid, payment_method, created_by, posted_at, posted_by, updated_at, created_at) VALUES (:s, :no, 'POSTED', :d, 5000, 0, 5000, 'PAID', 5000, 'CASH', :u, {NOW}, :u, {NOW}, {NOW})"
            ),
            [
                {
                    "s": shop,
                    "no": f"QS/{i}",
                    "d": date.fromordinal(date(year - 1, 1, 1).toordinal() + (i % 600)).isoformat(),
                    "u": user,
                }
                for i in range(QUICK)
            ],
        )
        c.execute(
            text(
                f"INSERT INTO suppliers (shop_id, name, is_active, updated_at, created_at) VALUES (:s, :name, TRUE, {NOW}, {NOW})"
            ),
            [{"s": shop, "name": f"Supplier {i}"} for i in range(40)],
        )
        first_supplier = c.execute(
            text("SELECT MIN(id) FROM suppliers WHERE shop_id = :s"), {"s": shop}
        ).scalar()
        c.execute(
            text(
                f"INSERT INTO purchases (shop_id, supplier_id, purchase_no, purchase_date, total_amount, amount_paid, payment_method, status, posted_at, posted_by, created_by, updated_at, created_at) VALUES (:s, :sup, :no, :d, 300000, 300000, 'CASH', 'POSTED', {NOW}, :u, :u, {NOW}, {NOW})"
            ),
            [
                {
                    "s": shop,
                    "sup": first_supplier + (i % 40),
                    "no": f"PUR/{i}",
                    "d": date.fromordinal(date(year - 1, 1, 1).toordinal() + (i % 600)).isoformat(),
                    "u": user,
                }
                for i in range(PURCHASES)
            ],
        )
        c.execute(
            text(
                f"INSERT INTO purchase_items (shop_id, purchase_id, product_id, unit_id, quantity, unit_cost, discount, line_total, updated_at, created_at) SELECT shop_id, id, :fp + (id % {PRODUCTS}), :unit, 10000, 3000, 0, 300000, {NOW}, {NOW} FROM purchases WHERE shop_id = :s"
            ),
            {"s": shop, "fp": first_product, "unit": unit},
        )
    if engine.dialect.name == "postgresql":  # a real server refreshes planner statistics on its own (autovacuum); a bulk load in a test must ask
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("ANALYZE"))
    return {"shop": shop}


@pytest.fixture
def reader(big, session_factory):
    """A session opened AFTER the data was seeded (an older one would keep reading its earlier snapshot)."""
    with session_factory() as new_session:
        counts = new_session.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM sales), (SELECT COUNT(*) FROM products), (SELECT COUNT(*) FROM inventory_transactions)"
            )
        ).one()
        assert (
            counts[0] == SALES and counts[1] == PRODUCTS and counts[2] > SALES
        )  # the test really ran on a large dataset
        yield new_session


@pytest.fixture
def counted(engine):
    counter = {"n": 0}

    def before(*_args):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", before)
    yield counter
    event.remove(engine, "before_cursor_execute", before)


def _filters(preset=Preset.LAST_YEAR):
    return build_filters(TODAY, preset=preset, compare=CompareMode.PREVIOUS_PERIOD, limit=200)


def _measure(name, fn, counter, *, seconds, queries):
    counter["n"] = 0
    start = time.perf_counter()
    fn()
    took = time.perf_counter() - start
    TIMINGS[name] = (took, counter["n"])
    print(f"PERF {name}: {took * 1000:.0f} ms, {counter['n']} queries")  # noqa: T201
    assert took < seconds, f"{name} took {took:.2f}s (limit {seconds}s)"
    assert counter["n"] <= queries, f"{name} ran {counter['n']} queries (limit {queries}): an N+1?"


class TestReportsScale:
    def test_every_report_stays_quick_and_bounded(self, reader, tenant_a, counted):
        s, shop = reader, tenant_a.shop.id
        f = _filters(Preset.THIS_YEAR if TODAY.month > 6 else Preset.LAST_YEAR)
        # Both the previous and current year hold data; the period below spans up to a full year of it.
        checks = {
            "kpis (all 31)": lambda: kpis.compute(s, shop, f, TODAY),
            "executive dashboard": lambda: executive.build(s, shop, f, TODAY, None),
            "sales summary": lambda: sales.summary(s, shop, f),
            "sales trend (day)": lambda: sales.trend(s, shop, f, "day"),
            "sales products": lambda: sales.products(s, shop, f),
            "sales categories": lambda: sales.categories(s, shop, f),
            "sales payment methods": lambda: sales.payment_methods(s, shop, f),
            "inventory stock": lambda: inventory.stock(s, shop, f),
            "inventory turnover": lambda: inventory.turnover(s, shop, f),
            "inventory movers (dead)": lambda: inventory.movers(s, shop, f, "dead"),
            "inventory ageing": lambda: inventory.aging(s, shop, f),
            "inventory stock-outs": lambda: inventory.stock_outs(s, shop, f),
            "customers overview": lambda: customers.overview(s, shop, f, TODAY),
            "customers list": lambda: customers.customers(s, shop, f, TODAY),
            "customer segments": lambda: customers.segments(s, shop, f, TODAY),
            "cohorts (12 months)": lambda: cohorts.cohorts(s, shop, f, TODAY, 12),
            "suppliers spend": lambda: suppliers.suppliers(s, shop, f),
            "suppliers products": lambda: suppliers.products(s, shop, f),
            "finance summary": lambda: finance_reports.summary(s, shop, f),
            "finance trend (month)": lambda: finance_reports.trend(s, shop, f, "month"),
            "cross-module insights": lambda: crosslinks.build(s, shop, f, TODAY, None),
        }
        for name, fn in checks.items():
            _measure(name, fn, counted, seconds=15.0, queries=400)

    def test_builder_over_the_largest_dataset(self, reader, tenant_a, counted):
        f = _filters(Preset.LAST_YEAR)
        q = builder.validate(
            {
                "group_by": ["kind", "payment_method"],
                "aggregations": [
                    {"field": "total_amount", "op": "SUM"},
                    {"field": "total_amount", "op": "COUNT"},
                ],
            },
            "sales",
            None,
        )
        _measure(
            "custom report (sales, grouped)",
            lambda: builder.run(reader, tenant_a.shop.id, q, f, TODAY),
            counted,
            seconds=15.0,
            queries=10,
        )

    def test_a_large_export(self, reader, tenant_a, counted):
        from dataclasses import replace

        f = _filters(Preset.LAST_YEAR)
        _measure(
            "catalog run: inventory-stock (3000 rows)",
            lambda: catalog.run(
                reader, tenant_a.shop.id, "inventory-stock", replace(f, limit=50000), TODAY, None
            ),
            counted,
            seconds=15.0,
            queries=400,
        )

    def test_query_counts_do_not_grow_with_the_number_of_products(self, reader, tenant_a, counted):
        """The N+1 guard: the same report over 3000 products must not run thousands of queries."""
        f = _filters(Preset.LAST_YEAR)
        counted["n"] = 0
        inventory.stock(reader, tenant_a.shop.id, f)
        stock_queries = counted["n"]
        counted["n"] = 0
        sales.products(reader, tenant_a.shop.id, f)
        assert stock_queries < 100 and counted["n"] < 100


class TestIndexesServeTheAnalyticsQueries:
    """The hot analytics filters must be answered from an index (SQLite reports `SEARCH ... USING INDEX`), never a table scan."""

    PLANS = [
        (
            "quick sales in a date range",
            "SELECT id FROM quick_sales WHERE shop_id = 1 AND status = 'POSTED' AND sale_date BETWEEN '2026-01-01' AND '2026-03-01'",
        ),
        (
            "posted purchases in a date range",
            "SELECT id FROM purchases WHERE shop_id = 1 AND status = 'POSTED' AND purchase_date BETWEEN '2026-01-01' AND '2026-03-01'",
        ),
        (
            "sales in a date range",
            "SELECT id FROM sales WHERE shop_id = 1 AND status = 'POSTED' AND sale_date BETWEEN '2026-01-01' AND '2026-03-01'",
        ),
        (
            "stock ledger of a product",
            "SELECT qty_delta FROM inventory_transactions WHERE shop_id = 1 AND product_id = 5 AND txn_date <= '2026-03-01'",
        ),
        ("lines of a sale", "SELECT id FROM sale_items WHERE shop_id = 1 AND sale_id = 5"),
        ("lines of a purchase", "SELECT id FROM purchase_items WHERE shop_id = 1 AND purchase_id = 5"),
    ]

    @pytest.mark.parametrize(("label", "sql"), PLANS, ids=[p[0] for p in PLANS])
    def test_the_plan_uses_an_index(self, label, sql, big, engine):
        with engine.connect() as c:
            plan = " | ".join(str(r[3]) for r in c.execute(text(f"EXPLAIN QUERY PLAN {sql}")))
        assert "USING" in plan and "SCAN" not in plan.replace("USING", ""), f"{label}: {plan}"
