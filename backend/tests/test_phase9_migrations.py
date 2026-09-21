"""Migrations 0011 (return numbers) and 0012 (product images, image_intelligence feature) on populated databases."""

from alembic import command
from sqlalchemy import text

from app.db.engine import create_db_engine
from tests.conftest import alembic_config, sqlite_url

NOW = "'2026-09-01 10:00:00.000000'"


def seed_at_0010(url):
    command.upgrade(alembic_config(url), "0010")
    engine = create_db_engine(url)
    with engine.begin() as c:
        c.execute(
            text(
                f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES (7, 'S', 'BAKERY', '9', 'x', 'WARN', {NOW}, {NOW})"
            )
        )
        c.execute(
            text(
                f"INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (3, 7, 'o@x.l', '!', 'O', 'OWNER', 1, {NOW}, {NOW})"
            )
        )
        c.execute(
            text(
                f"INSERT INTO sales (id, shop_id, invoice_no, status, sale_date, subtotal, discount, promotion_discount, total_amount, payment_type, amount_paid, payment_method, created_by, posted_at, posted_by, created_at, updated_at) VALUES (5, 7, 'INV/1', 'POSTED', '2026-08-30', 10000, 0, 0, 10000, 'PAID', 10000, 'CASH', 3, {NOW}, 3, {NOW}, {NOW})"
            )
        )
        c.execute(
            text(
                f"INSERT INTO sales_returns (id, shop_id, sale_id, return_date, refund_mode, total_refund, status, created_by, created_at, updated_at) VALUES (9, 7, 5, '2026-08-31', 'CASH', 5000, 'POSTED', 3, {NOW}, {NOW})"
            )
        )
    engine.dispose()


class TestMigration0011:
    def test_existing_returns_get_legacy_numbers_and_everything_else_is_kept(self, tmp_path):
        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        command.upgrade(alembic_config(url), "0011")
        engine = create_db_engine(url)
        with engine.connect() as c:
            row = c.execute(
                text("SELECT return_no, total_refund, status FROM sales_returns WHERE id = 9")
            ).one()
            assert tuple(row) == ("SRT/LEGACY/9", 5000, "POSTED")
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        engine.dispose()

    def test_the_number_is_required_unique_per_shop_and_not_blank(self, tmp_path):
        import pytest

        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        command.upgrade(alembic_config(url), "head")
        insert = (
            "INSERT INTO sales_returns (shop_id, return_no, sale_id, return_date, refund_mode, total_refund, status, created_by, created_at, updated_at)"
            f" VALUES (7, :no, 5, '2026-09-01', 'CASH', 0, 'POSTED', 3, {NOW}, {NOW})"
        )
        engine = create_db_engine(url)
        with engine.connect() as c:
            with pytest.raises(Exception, match="UNIQUE"):
                c.execute(text(insert), {"no": "SRT/LEGACY/9"})
            c.rollback()
            with pytest.raises(Exception, match="return_no_not_blank"):
                c.execute(text(insert), {"no": "  "})
            c.rollback()
            with pytest.raises(Exception, match="NOT NULL"):
                c.execute(text(insert), {"no": None})
            c.rollback()
        engine.dispose()

    def test_downgrade_and_upgrade_again(self, tmp_path):
        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        config = alembic_config(url)
        command.upgrade(config, "head")
        command.downgrade(config, "0010")
        command.upgrade(config, "head")
        command.check(config)


class TestMigration0012:
    def test_the_new_feature_is_on_for_pro_only_and_existing_plans_are_otherwise_unchanged(self, tmp_path):
        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        command.upgrade(alembic_config(url), "0011")
        engine = create_db_engine(url)
        with engine.connect() as c:
            before = {
                tuple(r)
                for r in c.execute(
                    text(
                        "SELECT p.code, f.feature_key, f.enabled FROM plan_features f JOIN plans p ON p.id = f.plan_id"
                    )
                )
            }
        engine.dispose()
        command.upgrade(alembic_config(url), "0012")
        engine = create_db_engine(url)
        with engine.connect() as c:
            after = {
                tuple(r)
                for r in c.execute(
                    text(
                        "SELECT p.code, f.feature_key, f.enabled FROM plan_features f JOIN plans p ON p.id = f.plan_id"
                    )
                )
            }
            assert after - before == {
                ("free", "image_intelligence", 0),
                ("basic", "image_intelligence", 0),
                ("pro", "image_intelligence", 1),
            }
            assert before <= after  # nothing that existed was changed
            assert c.exec_driver_sql("SELECT count(*) FROM product_images").scalar() == 0
        engine.dispose()

    def test_the_product_image_rules_are_enforced(self, tmp_path):
        import pytest

        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        command.upgrade(alembic_config(url), "head")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO categories (id, shop_id, name, is_active, created_at, updated_at) VALUES (1, 7, 'C', 1, {NOW}, {NOW})"
                )
            )
            unit = c.exec_driver_sql("SELECT id FROM units LIMIT 1").scalar()
            c.execute(
                text(
                    f"INSERT INTO products (id, shop_id, sku, name, category_id, unit_id, reorder_level, selling_price, is_active, created_at, updated_at) VALUES (1, 7, 'P', 'P', 1, {unit}, 0, 100, 1, {NOW}, {NOW})"
                )
            )
        insert = (
            "INSERT INTO product_images (shop_id, product_id, sha256, content_type, size_bytes, width, height, storage_key, created_by, created_at, updated_at)"
            f" VALUES (7, :pid, :sha, 'image/png', :size, 10, 10, 'k', 3, {NOW}, {NOW})"
        )
        ok = {"pid": 1, "sha": "a" * 64, "size": 5}
        with engine.connect() as c:
            c.execute(text(insert), ok)
            with pytest.raises(Exception, match="UNIQUE"):
                c.execute(text(insert), ok)  # one kept photo per product
            c.rollback()
            with pytest.raises(Exception, match="sha256_is_64_chars"):
                c.execute(text(insert), {**ok, "sha": "short"})
            c.rollback()
            with pytest.raises(Exception, match="size_bytes_positive"):
                c.execute(text(insert), {**ok, "size": 0})
            c.rollback()
            with pytest.raises(Exception, match="FOREIGN KEY"):
                c.execute(text(insert), {**ok, "pid": 999})
            c.rollback()
        engine.dispose()

    def test_downgrade_removes_only_what_it_added(self, tmp_path):
        url = sqlite_url(tmp_path / "m.db")
        seed_at_0010(url)
        config = alembic_config(url)
        command.upgrade(config, "head")
        command.downgrade(config, "0011")
        engine = create_db_engine(url)
        with engine.connect() as c:
            assert (
                c.exec_driver_sql(
                    "SELECT count(*) FROM plan_features WHERE feature_key = 'image_intelligence'"
                ).scalar()
                == 0
            )
            assert c.exec_driver_sql("SELECT count(*) FROM plan_features").scalar() == 27
            names = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        engine.dispose()
        assert "product_images" not in names
        command.upgrade(config, "head")
        command.check(config)
