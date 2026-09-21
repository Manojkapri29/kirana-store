"""Structural guarantees about the schema, checked against the database produced by the migration."""

import re

import pytest
from sqlalchemy import Engine, Float, Numeric, inspect

from app.models import Base

GLOBAL_TABLES = {
    "shops",
    "units",
    "business_types",
    "plans",
    "plan_features",
    "system_admins",
    "admin_audit_logs",
    "support_access_grants",
    "system_events",
    "backup_records",
    "restore_records",
    "accounts",
    "roles",
    "role_permissions",
    "auth_sessions",
    "background_jobs",
}  # not owned by a shop
INSERT_ONLY_TABLES = ["inventory_transactions", "customer_ledger", "audit_log"]
POSTGRES_IDENTIFIER_LIMIT = 63


@pytest.fixture
def inspector(engine: Engine):
    return inspect(engine)


def shop_owned_tables(inspector) -> list[str]:
    return [t for t in inspector.get_table_names() if t not in GLOBAL_TABLES | {"alembic_version"}]


class TestNoStoredStockOrBalance:
    def test_products_has_exactly_the_planned_columns_and_no_stock_column(self, inspector):
        columns = {c["name"] for c in inspector.get_columns("products")}

        assert columns == {
            "id", "shop_id", "sku", "name", "brand", "category_id", "unit_id", "default_supplier_id",
            "reorder_level", "mrp", "selling_price", "purchase_price", "avg_cost", "barcode",
            "is_active", "created_at", "updated_at",
        }  # fmt: skip

    def test_no_table_stores_current_stock_or_a_running_balance(self, inspector):
        forbidden = re.compile(r"current_stock|stock_on_hand|stock_qty|quantity_on_hand|balance|outstanding")
        offenders = [
            f"{table}.{column['name']}"
            for table in inspector.get_table_names()
            for column in inspector.get_columns(table)
            if forbidden.search(column["name"])
        ]

        assert offenders == []


class TestExactNumbersOnly:
    def test_no_float_or_numeric_columns_in_the_models(self):
        offenders = [
            f"{table.name}.{column.name}"
            for table in Base.metadata.tables.values()
            for column in table.columns
            if isinstance(column.type, Float | Numeric)
        ]

        assert offenders == []

    def test_no_real_or_numeric_column_types_in_the_database(self, inspector):
        offenders = [
            f"{table}.{column['name']} is {column['type']}"
            for table in inspector.get_table_names()
            for column in inspector.get_columns(table)
            if re.search(r"REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL", str(column["type"]).upper())
        ]

        assert offenders == []

    def test_no_postgresql_only_types_in_the_models(self):
        offenders = [
            f"{table.name}.{column.name}"
            for table in Base.metadata.tables.values()
            for column in table.columns
            if "postgresql" in type(column.type).__module__
        ]

        assert offenders == []


class TestTenantIsolationDesign:
    def test_every_shop_owned_table_has_a_required_shop_id_pointing_at_shops(self, inspector):
        for table in shop_owned_tables(inspector):
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            assert "shop_id" in columns, table
            assert columns["shop_id"]["nullable"] is False, table
            assert any(
                fk["referred_table"] == "shops" and fk["constrained_columns"] == ["shop_id"]
                for fk in inspector.get_foreign_keys(table)
            ), f"{table}.shop_id has no foreign key to shops"

    def test_every_shop_owned_table_can_be_a_tenant_fk_target(self, inspector):
        """`UNIQUE (shop_id, id)` is what lets child tables pin themselves to the same shop."""
        missing = []
        for table in shop_owned_tables(inspector):
            unique_sets = [c["column_names"] for c in inspector.get_unique_constraints(table)]
            if (
                table not in {"document_sequences", "idempotency_keys", "audit_log"}
                and ["shop_id", "id"] not in unique_sets
            ):
                missing.append(table)

        assert missing == []

    def test_references_between_shop_owned_tables_are_composite_and_include_shop_id(self, inspector):
        """A row can only point at a row of the SAME shop, enforced by the database itself."""
        offenders = []
        for table in shop_owned_tables(inspector):
            for fk in inspector.get_foreign_keys(table):
                if fk["referred_table"] in GLOBAL_TABLES:
                    continue
                if fk["constrained_columns"][0] != "shop_id" or fk["referred_columns"] != ["shop_id", "id"]:
                    offenders.append(f"{table}: {fk['constrained_columns']} -> {fk['referred_table']}")

        assert offenders == []


class TestQuickSalesAreStructurallyMoneyOnly:
    def test_no_product_quantity_or_cost_columns(self, inspector):
        columns = {c["name"] for c in inspector.get_columns("quick_sales")}

        assert not columns & {
            "product_id",
            "quantity",
            "qty",
            "unit_cost",
            "cogs_amount",
            "unit_price",
            "sale_id",
        }

    def test_cannot_reference_products_or_sale_lines(self, inspector):
        referred = {fk["referred_table"] for fk in inspector.get_foreign_keys("quick_sales")}

        assert referred <= {"shops", "customers", "users", "quick_sales"}

    def test_there_is_no_line_item_table_for_quick_sales(self, inspector):
        assert not [
            t for t in inspector.get_table_names() if t.startswith("quick_sale") and t != "quick_sales"
        ]


class TestInsertOnlyTablesHaveNoUpdatedAt:
    @pytest.mark.parametrize("table", INSERT_ONLY_TABLES)
    def test_ledger_rows_have_a_creation_time_but_no_update_time(self, inspector, table):
        columns = {c["name"] for c in inspector.get_columns(table)}

        assert "created_at" in columns
        assert "updated_at" not in columns


class TestPostgresCompatibleNames:
    def test_all_identifiers_fit_postgresql_63_character_limit(self, inspector):
        too_long = []
        for table in inspector.get_table_names():
            names = [table, *(c["name"] for c in inspector.get_columns(table))]
            names += [c["name"] for c in inspector.get_unique_constraints(table)]
            names += [c["name"] for c in inspector.get_check_constraints(table)]
            names += [c["name"] for c in inspector.get_foreign_keys(table)]
            names += [c["name"] for c in inspector.get_indexes(table)]
            names.append(inspector.get_pk_constraint(table)["name"])
            too_long += [n for n in names if n and len(n) > POSTGRES_IDENTIFIER_LIMIT]

        assert too_long == []

    def test_every_constraint_has_an_explicit_name(self, inspector):
        unnamed = []
        for table in inspector.get_table_names():
            for kind, items in [
                ("unique", inspector.get_unique_constraints(table)),
                ("check", inspector.get_check_constraints(table)),
                ("foreign key", inspector.get_foreign_keys(table)),
            ]:
                unnamed += [f"{table} {kind}" for item in items if not item["name"]]

        assert unnamed == []
