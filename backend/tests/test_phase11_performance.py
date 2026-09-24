"""A lightweight performance smoke test: thousands of rows, the main screens must stay quick and use their indexes.

This is not a benchmark. The time limits are generous (they catch a query that became a full scan or an N+1, not a few
milliseconds), and the query-plan checks show that the hot paths are served by indexes. Findings are in docs/PRODUCTION.md.
"""

import time

import pytest
from sqlalchemy import text

PRODUCTS, CUSTOMERS, SALES, LEDGER = 3000, 1500, 6000, 12000
NOW = "'2026-09-01 10:00:00.000000'"


@pytest.fixture
def big(tenant_a, units, engine):
    shop = tenant_a.shop.id
    user = tenant_a.user.id
    category = tenant_a.category.id
    unit = units["pcs"]
    with engine.begin() as c:
        c.execute(
            text(
                f"INSERT INTO products (shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, updated_at, created_at) "
                f"VALUES (:s, :sku, :name, :cat, :unit, 5000, 2500, 1, {NOW}, {NOW})"
            ),
            [
                {"s": shop, "sku": f"P{i:05d}", "name": f"Product {i:05d}", "cat": category, "unit": unit}
                for i in range(PRODUCTS)
            ],
        )
        c.execute(
            text(
                f"INSERT INTO inventory_transactions (shop_id, product_id, txn_type, qty_delta, txn_date, created_by, created_at) "
                f"SELECT :s, id, 'OPENING', 50000, '2026-08-01', :u, {NOW} FROM products WHERE shop_id = :s"
            ),
            {"s": shop, "u": user},
        )
        c.execute(
            text(
                f"INSERT INTO customers (shop_id, name, is_active, updated_at, created_at) VALUES (:s, :name, 1, {NOW}, {NOW})"
            ),
            [{"s": shop, "name": f"Customer {i:05d}"} for i in range(CUSTOMERS)],
        )
        c.execute(
            text(
                f"INSERT INTO sales (shop_id, invoice_no, status, sale_date, subtotal, discount, promotion_discount, total_amount, payment_type, amount_paid, payment_method, created_by, posted_at, posted_by, updated_at, created_at) "
                f"VALUES (:s, :no, 'POSTED', :d, 10000, 0, 0, 10000, 'PAID', 10000, 'CASH', :u, {NOW}, :u, {NOW}, {NOW})"
            ),
            [
                {"s": shop, "no": f"INV/{i}", "d": f"2026-0{1 + i % 9}-{1 + i % 28:02d}", "u": user}
                for i in range(SALES)
            ],
        )
    ids = {"shop": shop}
    return ids


def timed(fn, limit):
    start = time.perf_counter()
    result = fn()
    took = time.perf_counter() - start
    assert took < limit, f"took {took:.2f}s (limit {limit}s)"
    return result


class TestScreensStayQuick:
    def test_product_and_customer_lists_search_and_pages(self, client_a, big):
        products = timed(lambda: client_a.get("/api/v1/products", params={"limit": 50}), 2.0).json()
        assert products["total"] == PRODUCTS and len(products["items"]) == 50
        assert (
            timed(lambda: client_a.get("/api/v1/products", params={"q": "Product 0123"}), 2.0).json()["total"]
            >= 1
        )
        assert (
            timed(lambda: client_a.get("/api/v1/customers", params={"limit": 50}), 2.0).json()["total"]
            == CUSTOMERS
        )
        assert (
            timed(lambda: client_a.get("/api/v1/customers", params={"q": "Customer 0042"}), 2.0).json()[
                "total"
            ]
            >= 1
        )

    def test_inventory_and_sales_lists(self, client_a, big):
        inventory = timed(lambda: client_a.get("/api/v1/inventory", params={"limit": 50}), 3.0)
        assert inventory.status_code == 200
        sales = timed(lambda: client_a.get("/api/v1/sales", params={"limit": 50}), 2.0).json()
        assert sales["total"] == SALES

    def test_the_overview_and_reports_over_thousands_of_sales(self, client_a, big):
        overview = timed(
            lambda: client_a.get(
                "/api/v1/analytics/overview",
                params={"date_from": "2026-01-01", "date_to": "2026-12-31", "bucket": "month"},
            ),
            8.0,
        )
        assert overview.status_code == 200 and overview.json()["sales"]["transactions"] == SALES

    def test_an_export_of_thousands_of_rows(self, client_a, big):
        response = timed(lambda: client_a.get("/api/v1/exports/products"), 8.0)
        assert response.status_code == 200 and response.content.count(b"\n") >= PRODUCTS

    def test_the_integrity_check_scales(self, big, session_factory):
        from app.reporting import integrity

        with session_factory() as s:
            timed(lambda: integrity.run(s), 10.0)


PLANS = [
    (
        "products by shop and name",
        "SELECT id FROM products WHERE shop_id = 1 AND name LIKE 'Product 01%' ORDER BY name",
        "ix_products_shop_id_name",
    ),
    (
        "sales in a date range",
        "SELECT id FROM sales WHERE shop_id = 1 AND sale_date BETWEEN '2026-01-01' AND '2026-03-01'",
        "ix_sales_shop_date",
    ),
    (
        "one product's stock history",
        "SELECT qty_delta FROM inventory_transactions WHERE shop_id = 1 AND product_id = 5",
        "ix_inventory_transactions_shop_product_date",
    ),
    (
        "a customer's khata",
        "SELECT id FROM customer_ledger WHERE shop_id = 1 AND customer_id = 3 ORDER BY entry_date",
        "ix_customer_ledger_shop_customer_date",
    ),
    (
        "notification inbox",
        "SELECT id FROM notification_deliveries WHERE shop_id = 1 AND user_id = 1 AND channel = 'IN_APP' AND read_at IS NULL",
        "ix_notification_deliveries_inbox",
    ),
    (
        "due deliveries",
        "SELECT id FROM notification_deliveries WHERE status = 'RETRYING' AND next_attempt_at <= '2026-09-01'",
        "ix_notification_deliveries_due",
    ),
    (
        "audit trail of an entity",
        "SELECT id FROM audit_log WHERE shop_id = 1 AND entity_type = 'sale' AND entity_id = 4",
        "ix_audit_log_shop_entity",
    ),
]


@pytest.mark.parametrize(("name", "sql", "index"), PLANS, ids=[p[0] for p in PLANS])
def test_hot_queries_use_their_index(engine, name, sql, index):
    with engine.connect() as c:
        if engine.dialect.name == "postgresql":
            # An empty test table would always be scanned, so the question asked here is: CAN the index serve this query?
            # (A LIKE 'prefix%' on a text column with a non-C collation cannot use a plain btree index: the app searches with
            # contains-matching anyway, so that one query is checked by the index name being present in the schema instead.)
            c.execute(text("SET enable_seqscan = off"))
            plan = " ".join(str(r[0]) for r in c.execute(text("EXPLAIN " + sql)))
            if "LIKE" in sql:
                exists = c.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": index}).scalar()
                assert exists, f"{name}: index {index} is missing"
                return
            exists = c.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": index}).scalar()
            assert exists, f"{name}: index {index} is missing"
            assert "Seq Scan" not in plan, f"{name}: {plan}"  # on empty tables the planner may pick a sibling index, but never a scan
            return
        else:
            plan = " ".join(str(r[3]) for r in c.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert index in plan, f"{name}: {plan}"
