"""Alembic: the database can be built from scratch, is in sync with the models, and can be rolled back."""

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.core.config import BACKEND_DIR
from app.db.engine import create_db_engine
from app.models import Base
from tests.conftest import alembic_config, sqlite_url

EXPECTED_TABLES = {
    "audit_log", "business_types", "categories", "customer_ledger", "customers", "document_sequences",
    "expense_categories", "expenses", "idempotency_keys", "inventory_transactions", "products",
    "purchase_items", "purchase_return_items", "purchase_returns", "purchases", "quick_sales",
    "sale_items", "sales", "sales_return_items", "sales_returns", "shops", "suppliers", "units", "users",
}  # fmt: skip

MIGRATION_FILES = sorted((BACKEND_DIR / "migrations" / "versions").glob("*.py"))


@pytest.fixture
def blank_db_url(tmp_path: Path) -> str:
    return sqlite_url(tmp_path / "blank.db")


def table_names(url: str) -> set[str]:
    engine = create_db_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def current_revision(url: str) -> str | None:
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_upgrade_from_an_empty_database_creates_every_table(blank_db_url: str):
    assert table_names(blank_db_url) == set()

    command.upgrade(alembic_config(blank_db_url), "head")

    assert table_names(blank_db_url) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == len(EXPECTED_TABLES)


def test_migration_state_is_at_head_and_matches_the_models(blank_db_url: str):
    config = alembic_config(blank_db_url)
    command.upgrade(config, "head")

    head = ScriptDirectory.from_config(config).get_current_head()
    assert head == "0004"
    assert current_revision(blank_db_url) == head

    # `alembic check` raises if autogenerate would produce any change (models and DB disagree).
    command.check(config)


def test_there_is_a_single_migration_head():
    heads = ScriptDirectory.from_config(alembic_config("sqlite:///unused.db")).get_heads()
    assert heads == ["0004"]


def test_downgrade_removes_everything_and_upgrade_can_run_again(blank_db_url: str):
    config = alembic_config(blank_db_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")
    assert table_names(blank_db_url) == set()
    engine = create_db_engine(blank_db_url)
    with engine.connect() as connection:
        leftover = connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'"))
    engine.dispose()
    assert leftover == 0

    command.upgrade(config, "head")
    assert table_names(blank_db_url) == EXPECTED_TABLES


def test_units_are_seeded_by_the_migration(session):
    rows = session.execute(text("SELECT code, name, allows_decimal FROM units ORDER BY id")).all()

    assert [tuple(row) for row in rows] == [
        ("pcs", "Piece", 0),
        ("kg", "Kilogram", 1),
        ("g", "Gram", 0),
        ("L", "Litre", 1),
        ("ml", "Millilitre", 0),
        ("pkt", "Packet", 0),
        ("box", "Box", 0),
        ("doz", "Dozen", 1),
        # added by migration 0002 so that garments, footwear, cloth, drinks and trays are natural to count
        ("m", "Metre", 1),
        ("pair", "Pair", 0),
        ("btl", "Bottle", 0),
        ("tray", "Tray", 0),
    ]


def test_migration_is_self_contained():
    """Migrations must not import application code, or they would break when the models change."""
    for migration in MIGRATION_FILES:
        source = migration.read_text()
        assert not re.search(r"^\s*(from|import)\s+app\b", source, flags=re.MULTILINE), migration.name


def test_migration_renders_valid_looking_postgresql_ddl():
    """PostgreSQL is not installed in Phase 2, but the migration is rendered as PostgreSQL SQL (offline,
    no server needed) to catch SQLite-only constructs early. The full run on a real PostgreSQL server
    happens at the checkpoint after Phase 10."""
    config = alembic_config("postgresql://user:password@localhost/kirana")
    buffer = io.StringIO()
    config.output_buffer = buffer

    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()

    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE {table} " in sql
    assert "BIGSERIAL" in sql  # auto-increment ids
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "DEFAULT false" in sql and "DEFAULT true" in sql
    assert not re.search(r"BOOLEAN DEFAULT [01]\b", sql), "boolean defaults must not be SQLite-style 0/1"
    assert "CREATE FUNCTION kirana_forbid_change()" in sql
    assert sql.count("CREATE TRIGGER") == 3
    assert sql.count("INSERT INTO units") == 12
    assert "AUTOINCREMENT" not in sql


class TestMigration0002PreservesData:
    """0002 rebuilds `shops` (on SQLite) while other tables point at it. Prove nothing is lost or broken."""

    NOW = "'2026-09-01 10:00:00.000000'"

    def seed_revision_0001(self, url: str) -> None:
        """A database exactly as Phase 3 left it: a shop, user, category, product and ledger row."""
        command.upgrade(alembic_config(url), "0001")
        engine = create_db_engine(url)  # foreign keys ON: the seed data must itself be valid
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO shops (id, name, phone, address, mrp_validation_mode, allow_negative_stock, created_at, updated_at)"
                    f" VALUES (7, 'Old Shop', '9999999999', '1 Old Road', 'BLOCK', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at)"
                    f" VALUES (3, 7, 'o@old.local', '!', 'Old Owner', 'OWNER', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO categories (id, shop_id, name, is_active, created_at, updated_at) VALUES (5, 7, 'Grocery', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO products (id, shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, created_at, updated_at)"
                    f" VALUES (11, 7, 'RICE', 'Rice', 5, 2, 5000, 25050, 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO inventory_transactions (id, shop_id, product_id, txn_type, qty_delta, txn_date, reference_type, reference_id, created_by, created_at)"
                    f" VALUES (21, 7, 11, 'OPENING', 25500, '2026-09-01', 'PRODUCT', 11, 3, {self.NOW})"
                )
            )
        engine.dispose()

    def test_existing_data_survives_and_the_shop_becomes_grocery(self, blank_db_url: str):
        self.seed_revision_0001(blank_db_url)

        command.upgrade(alembic_config(blank_db_url), "head")

        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            shop = c.execute(
                text(
                    "SELECT id, name, business_type, mrp_validation_mode, allow_negative_stock, created_at FROM shops"
                )
            ).one()
            assert tuple(shop)[:5] == (
                7,
                "Old Shop",
                "GROCERY",
                "BLOCK",
                1,
            )  # every column kept, type backfilled
            assert str(shop.created_at).startswith("2026-09-01 10:00:00")
            assert c.scalar(text("SELECT full_name FROM users WHERE id = 3")) == "Old Owner"
            assert c.scalar(text("SELECT name FROM categories WHERE id = 5")) == "Grocery"
            assert c.execute(
                text("SELECT sku, selling_price, reorder_level FROM products WHERE id = 11")
            ).one() == ("RICE", 25050, 5000)
            assert c.scalar(text("SELECT qty_delta FROM inventory_transactions WHERE id = 21")) == 25500
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []  # nothing left dangling
        engine.dispose()

    def test_the_rebuilt_shops_table_keeps_its_rules_and_the_business_type_has_no_default(
        self, blank_db_url: str
    ):
        self.seed_revision_0001(blank_db_url)
        command.upgrade(alembic_config(blank_db_url), "head")
        engine = create_db_engine(blank_db_url)

        with engine.connect() as c:
            ddl = c.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'shops'"))
            for constraint in (
                "ck_shops_name_not_blank",
                "ck_shops_language",
                "ck_shops_mrp_validation_mode",
                "fk_shops_business_type",
            ):
                assert constraint in ddl, constraint
            assert (
                re.search(r"business_type VARCHAR\(30\) NOT NULL[,\s]", ddl)
                and "DEFAULT 'GROCERY'" not in ddl
            )
            # the rules still bite: a nameless shop and an unknown type are refused
            for values in ("'', 'GROCERY'", "'X', 'NOPE'"):
                with pytest.raises(Exception, match="constraint"):
                    c.execute(
                        text(
                            "INSERT INTO shops (name, business_type, phone, address, mrp_validation_mode, created_at, updated_at)"
                            f" VALUES ({values}, '1', 'x', 'WARN', {self.NOW}, {self.NOW})"
                        )
                    )
                c.rollback()
        engine.dispose()

    def test_tables_that_point_at_shops_still_work_and_stay_protected(self, blank_db_url: str):
        self.seed_revision_0001(blank_db_url)
        command.upgrade(alembic_config(blank_db_url), "head")
        engine = create_db_engine(blank_db_url)

        with engine.begin() as c:  # a new product for the OLD shop: composite foreign keys still resolve
            c.execute(
                text(
                    "INSERT INTO products (shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, created_at, updated_at)"
                    f" VALUES (7, 'PAIR', 'Sandals', 5, (SELECT id FROM units WHERE code = 'pair'), 0, 9900, 1, {self.NOW}, {self.NOW})"
                )
            )
        with engine.connect() as c:
            with pytest.raises(Exception, match="insert-only"):
                c.execute(text("UPDATE inventory_transactions SET qty_delta = 1"))  # ledger trigger survived
        engine.dispose()
        command.check(alembic_config(blank_db_url))  # and models still equal the migrated schema

    def test_downgrade_returns_to_revision_0001_and_upgrade_works_again(self, blank_db_url: str):
        self.seed_revision_0001(blank_db_url)
        config = alembic_config(blank_db_url)
        command.upgrade(config, "head")

        command.downgrade(config, "0001")

        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            columns = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(shops)")}
            assert "business_type" not in columns and "name" in columns
            assert c.scalar(text("SELECT count(*) FROM sqlite_master WHERE name = 'business_types'")) == 0
            assert c.scalar(text("SELECT count(*) FROM units")) == 8
            assert c.scalar(text("SELECT name FROM shops WHERE id = 7")) == "Old Shop"
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        engine.dispose()
        command.upgrade(config, "head")
        assert current_revision(blank_db_url) == "0004"


class TestMigration0003PreservesData:
    """0003 adds optional supplier columns and two indexes. Existing suppliers and product links must survive."""

    NOW = "'2026-09-01 10:00:00.000000'"

    def seed_revision_0002(self, url: str) -> None:
        command.upgrade(alembic_config(url), "0002")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at)"
                    f" VALUES (7, 'Old Shop', 'BAKERY', '9999999999', '1 Old Road', 'WARN', {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at)"
                    f" VALUES (3, 7, 'o@old.local', '!', 'Old Owner', 'OWNER', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO categories (id, shop_id, name, is_active, created_at, updated_at) VALUES (5, 7, 'Bread', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO suppliers (id, shop_id, name, phone, address, gstin, is_active, created_at, updated_at)"
                    f" VALUES (9, 7, 'Flour Mill', '9876543210', 'Mill Road', '27AAPFU0939F1ZV', 0, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO products (id, shop_id, sku, name, category_id, unit_id, default_supplier_id, reorder_level, selling_price, is_active, created_at, updated_at)"
                    f" VALUES (11, 7, 'BREAD', 'Bread', 5, 1, 9, 0, 4000, 1, {self.NOW}, {self.NOW})"
                )
            )
        engine.dispose()

    def test_existing_suppliers_and_product_links_survive(self, blank_db_url: str):
        self.seed_revision_0002(blank_db_url)

        command.upgrade(alembic_config(blank_db_url), "head")

        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT name, phone, address, gstin, is_active, alternate_phone, email, notes FROM suppliers WHERE id = 9"
                )
            ).one()
            assert tuple(row) == (
                "Flour Mill",
                "9876543210",
                "Mill Road",
                "27AAPFU0939F1ZV",
                0,
                None,
                None,
                None,
            )
            assert c.scalar(text("SELECT default_supplier_id FROM products WHERE id = 11")) == 9
            indexes = {r[1] for r in c.exec_driver_sql("PRAGMA index_list(suppliers)")} | {
                r[1] for r in c.exec_driver_sql("PRAGMA index_list(products)")
            }
            assert {"ix_suppliers_shop_id_name", "ix_products_shop_id_default_supplier_id"} <= indexes
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            # the tenant rule still bites: another shop's supplier cannot be attached to this shop's product
            c.execute(
                text(
                    f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES (8, 'Other', 'OTHER', '1', 'x', 'WARN', {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO suppliers (id, shop_id, name, is_active, created_at, updated_at) VALUES (20, 8, 'Theirs', 1, {self.NOW}, {self.NOW})"
                )
            )
            with pytest.raises(Exception, match="FOREIGN KEY"):
                c.execute(text("UPDATE products SET default_supplier_id = 20 WHERE id = 11"))
            c.rollback()
        engine.dispose()
        command.check(alembic_config(blank_db_url))

    def test_downgrade_keeps_the_original_columns_and_upgrade_works_again(self, blank_db_url: str):
        self.seed_revision_0002(blank_db_url)
        config = alembic_config(blank_db_url)
        command.upgrade(config, "head")

        command.downgrade(config, "0002")

        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            columns = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(suppliers)")}
            assert (
                columns.isdisjoint({"alternate_phone", "email", "notes"})
                and {"name", "phone", "gstin"} <= columns
            )
            assert c.scalar(text("SELECT name FROM suppliers WHERE id = 9")) == "Flour Mill"
            assert c.scalar(text("SELECT default_supplier_id FROM products WHERE id = 11")) == 9
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        engine.dispose()
        command.upgrade(config, "head")
        assert current_revision(blank_db_url) == "0004"


class TestMigration0004PreservesData:
    """0004 turns the purchase tables into a workflow. Purchases that already exist must survive intact."""

    NOW = "'2026-09-01 10:00:00.000000'"

    def seed_revision_0003(self, url: str) -> None:
        command.upgrade(alembic_config(url), "0003")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at)"
                    f" VALUES (7, 'Old Shop', 'BAKERY', '9999999999', '1 Old Road', 'WARN', {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at)"
                    f" VALUES (3, 7, 'o@old.local', '!', 'Old Owner', 'OWNER', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO categories (id, shop_id, name, is_active, created_at, updated_at) VALUES (5, 7, 'Bread', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO suppliers (id, shop_id, name, is_active, created_at, updated_at) VALUES (9, 7, 'Flour Mill', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO products (id, shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, created_at, updated_at)"
                    f" VALUES (11, 7, 'BREAD', 'Bread', 5, 2, 0, 4000, 1, {self.NOW}, {self.NOW})"
                )
            )
            # A purchase that was already POSTED under the old rules, with one line, and a voided one.
            c.execute(
                text(
                    "INSERT INTO purchases (id, shop_id, supplier_id, supplier_invoice_no, purchase_date, total_amount, amount_paid,"
                    " created_by, status, created_at, updated_at)"
                    f" VALUES (21, 7, 9, 'INV-1', '2026-08-30', 10000, 0, 3, 'POSTED', '2026-08-30 09:00:00.000000', {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO purchase_items (id, shop_id, purchase_id, product_id, quantity, unit_cost, line_total, created_at, updated_at)"
                    f" VALUES (31, 7, 21, 11, 5000, 2000, 10000, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    "INSERT INTO purchases (id, shop_id, supplier_id, supplier_invoice_no, purchase_date, total_amount, amount_paid,"
                    " created_by, status, void_reason, voided_at, created_at, updated_at)"
                    f" VALUES (22, 7, 9, 'INV-2', '2026-08-31', 500, 0, 3, 'VOID', 'Entered twice', {self.NOW}, {self.NOW}, {self.NOW})"
                )
            )
        engine.dispose()

    def test_existing_purchases_are_numbered_and_lines_get_their_unit(self, blank_db_url: str):
        self.seed_revision_0003(blank_db_url)

        command.upgrade(alembic_config(blank_db_url), "head")

        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            posted = c.execute(
                text(
                    "SELECT status, purchase_no, posted_at, created_at, total_amount FROM purchases WHERE id = 21"
                )
            ).one()
            assert posted.status == "POSTED" and posted.purchase_no == "PUR/LEGACY/21"
            assert posted.posted_at == posted.created_at and posted.total_amount == 10000
            void = c.execute(
                text("SELECT status, purchase_no, void_reason FROM purchases WHERE id = 22")
            ).one()
            assert tuple(void) == ("VOID", None, "Entered twice")

            item = c.execute(
                text(
                    "SELECT unit_id, discount, stock_before, avg_cost_before, avg_cost_after, quantity, line_total"
                    " FROM purchase_items WHERE id = 31"
                )
            ).one()
            assert tuple(item) == (2, 0, None, None, None, 5000, 10000)  # unit copied from the product
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        engine.dispose()
        command.check(alembic_config(blank_db_url))  # the migrated schema equals the models

    def test_new_rules_are_enforced_on_the_migrated_database(self, blank_db_url: str):
        self.seed_revision_0003(blank_db_url)
        command.upgrade(alembic_config(blank_db_url), "head")
        insert = (
            "INSERT INTO purchases (shop_id, supplier_id, supplier_invoice_no, purchase_date, total_amount,"
            " amount_paid, created_by, status, purchase_no, posted_at, void_reason, created_at, updated_at)"
            " VALUES (7, 9, :inv, '2026-09-01', 0, 0, 3, :status, :no, :posted_at, :reason,"
            f" {self.NOW}, {self.NOW})"
        )
        engine = create_db_engine(blank_db_url)
        with engine.connect() as c:
            draft = {"status": "DRAFT", "no": None, "posted_at": None, "reason": None}
            # a draft is fine, but a second live purchase with the same supplier invoice number is not
            c.execute(text(insert), {**draft, "inv": "NEW-1"})
            with pytest.raises(Exception, match="UNIQUE"):
                c.execute(text(insert), {**draft, "inv": "NEW-1"})
            c.rollback()
            # a VOID one releases its number, so INV-2 (already void) can be used again by a live purchase
            c.execute(text(insert), {**draft, "inv": "INV-2"})
            c.rollback()
            # a draft may not carry a purchase number; a posted purchase must
            with pytest.raises(Exception, match="CHECK"):
                c.execute(
                    text(insert), {**draft, "inv": "X", "no": "PUR/1", "posted_at": self.NOW.strip("'")}
                )
            c.rollback()
            with pytest.raises(Exception, match="CHECK"):
                c.execute(text(insert), {**draft, "inv": "X", "status": "POSTED"})
            c.rollback()
            with pytest.raises(Exception, match="CHECK"):  # VOID needs a reason
                c.execute(text(insert), {**draft, "inv": "X", "status": "VOID"})
            c.rollback()
        engine.dispose()

    def test_downgrade_is_refused_while_drafts_exist_and_works_once_they_are_gone(self, blank_db_url: str):
        self.seed_revision_0003(blank_db_url)
        config = alembic_config(blank_db_url)
        command.upgrade(config, "head")
        engine = create_db_engine(blank_db_url)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO purchases (id, shop_id, supplier_id, purchase_date, total_amount, amount_paid,"
                    " created_by, status, created_at, updated_at)"
                    f" VALUES (40, 7, 9, '2026-09-01', 0, 0, 3, 'DRAFT', {self.NOW}, {self.NOW})"
                )
            )

        with pytest.raises(RuntimeError, match="draft purchase"):
            command.downgrade(config, "0003")
        assert current_revision(blank_db_url) == "0004"  # untouched

        with engine.begin() as c:
            c.execute(text("DELETE FROM purchases WHERE id = 40"))
        command.downgrade(config, "0003")

        with engine.connect() as c:
            columns = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(purchases)")}
            assert columns.isdisjoint({"purchase_no", "posted_at", "posted_by"})
            item_columns = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(purchase_items)")}
            assert item_columns.isdisjoint({"unit_id", "discount", "stock_before"})
            assert c.scalar(text("SELECT count(*) FROM purchases")) == 2
            assert c.scalar(text("SELECT count(*) FROM purchase_items")) == 1
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        engine.dispose()
        command.upgrade(config, "head")
        assert current_revision(blank_db_url) == "0004"
