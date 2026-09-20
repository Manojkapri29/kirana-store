"""Money, Quantity and UTC timestamp storage. No floating-point anywhere."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.types import (
    from_thousandths,
    paise_to_rupees,
    round_money,
    round_quantity,
    rupees_to_paise,
    to_thousandths,
)
from app.models import Product, Shop
from tests import factories


def raw(session: Session, sql: str) -> object:
    return session.execute(text(sql)).scalar_one()


class TestMoneyConversion:
    @pytest.mark.parametrize(
        ("value", "paise"),
        [
            (Decimal("25.50"), 2550),
            ("25.50", 2550),
            (Decimal("0.01"), 1),
            (Decimal("0"), 0),
            (25, 2500),
            (Decimal("-10.05"), -1005),
            (Decimal("1234567.89"), 123456789),
        ],
    )
    def test_rupees_to_paise_is_exact(self, value, paise):
        assert rupees_to_paise(value) == paise

    def test_paise_back_to_rupees_keeps_two_decimals(self):
        assert paise_to_rupees(2550) == Decimal("25.50")
        assert str(paise_to_rupees(2550)) == "25.50"
        assert str(paise_to_rupees(5)) == "0.05"
        assert str(paise_to_rupees(0)) == "0.00"

    def test_floats_are_rejected(self):
        with pytest.raises(TypeError, match="Floating-point"):
            rupees_to_paise(25.5)

    def test_booleans_are_rejected(self):
        with pytest.raises(TypeError):
            rupees_to_paise(True)

    def test_extra_decimal_places_are_rejected_not_rounded(self):
        with pytest.raises(ValueError, match="more than 2 decimal places"):
            rupees_to_paise(Decimal("10.005"))

    @pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity"])
    def test_garbage_is_rejected(self, bad):
        with pytest.raises(ValueError):
            rupees_to_paise(bad)

    def test_rounding_helper_is_half_up_and_exact(self):
        # In binary floating point 2.675 is stored as 2.67499999..., which is why floats are banned.
        assert round_money(Decimal("2.675")) == Decimal("2.68")
        assert round_money(Decimal("0.005")) == Decimal("0.01")
        assert round_money(Decimal("0.004")) == Decimal("0.00")


class TestQuantityConversion:
    @pytest.mark.parametrize(
        ("value", "thousandths"),
        [
            (Decimal("2.5"), 2500),
            ("0.250", 250),
            (Decimal("0.001"), 1),
            (7, 7000),
            (Decimal("-1.5"), -1500),
        ],
    )
    def test_to_thousandths_is_exact(self, value, thousandths):
        assert to_thousandths(value) == thousandths

    def test_from_thousandths_keeps_three_decimals(self):
        assert from_thousandths(2500) == Decimal("2.500")
        assert str(from_thousandths(250)) == "0.250"
        assert str(from_thousandths(7000)) == "7.000"

    def test_floats_and_fourth_decimal_are_rejected(self):
        with pytest.raises(TypeError):
            to_thousandths(2.5)
        with pytest.raises(ValueError, match="more than 3 decimal places"):
            to_thousandths(Decimal("0.0005"))

    def test_rounding_helper(self):
        assert round_quantity(Decimal("0.0005")) == Decimal("0.001")


class TestMoneyStoredInDatabase:
    def test_25_50_is_stored_as_the_integer_2550(self, session: Session, tenant_a):
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, selling_price=Decimal("25.50")
        )
        session.commit()

        stored = raw(session, f"SELECT selling_price FROM products WHERE id = {product.id}")
        column_type = raw(session, f"SELECT typeof(selling_price) FROM products WHERE id = {product.id}")

        assert stored == 2550
        assert column_type == "integer"  # never 'real'

    def test_value_read_back_is_an_exact_decimal(self, session: Session, tenant_a):
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, selling_price=Decimal("25.50")
        )
        session.commit()
        session.expire_all()

        loaded = session.get(Product, product.id)
        assert loaded.selling_price == Decimal("25.50")
        assert isinstance(loaded.selling_price, Decimal)

    def test_null_stays_null_and_is_never_zero(self, session: Session, tenant_a):
        product = factories.make_product(session, tenant_a.shop, tenant_a.category)  # no MRP, no costs
        session.commit()
        session.expire_all()

        loaded = session.get(Product, product.id)
        assert loaded.mrp is None
        assert loaded.purchase_price is None
        assert loaded.avg_cost is None

    def test_repeated_small_amounts_add_up_exactly(self, session: Session, tenant_a):
        """0.10 added ten times is exactly 1.00, summed by the database on exact integers."""
        for i in range(10):
            factories.make_product(
                session, tenant_a.shop, tenant_a.category, sku=f"S{i}", selling_price=Decimal("0.10")
            )
        session.commit()

        total = session.scalar(select(func.sum(Product.selling_price)))

        assert total == Decimal("1.00")
        assert 0.1 + 0.2 != 0.3  # the float behaviour this design avoids

    def test_comparisons_in_sql_use_exact_values(self, session: Session, tenant_a):
        factories.make_product(
            session, tenant_a.shop, tenant_a.category, sku="A", selling_price=Decimal("10.00")
        )
        factories.make_product(
            session, tenant_a.shop, tenant_a.category, sku="B", selling_price=Decimal("10.01")
        )
        session.commit()

        above = session.scalars(select(Product.sku).where(Product.selling_price > Decimal("10.00"))).all()

        assert above == ["B"]


class TestQuantityStoredInDatabase:
    def test_fractional_quantity_is_stored_as_thousandths(self, session: Session, tenant_a):
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, reorder_level=Decimal("2.5")
        )
        session.commit()

        stored = raw(session, f"SELECT reorder_level FROM products WHERE id = {product.id}")
        assert stored == 2500

        session.expire_all()
        assert session.get(Product, product.id).reorder_level == Decimal("2.500")

    def test_default_reorder_level_is_zero(self, session: Session, tenant_a):
        product = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()
        session.expire_all()

        assert session.get(Product, product.id).reorder_level == Decimal("0.000")

    def test_a_float_quantity_cannot_be_saved(self, session: Session, tenant_a):
        with pytest.raises(Exception, match="Floating-point"):
            factories.make_product(session, tenant_a.shop, tenant_a.category, reorder_level=2.5)
        session.rollback()


class TestUtcDateTimeType:
    def test_aware_datetime_in_another_timezone_is_stored_as_utc(self, session: Session):
        ist = timezone(timedelta(hours=5, minutes=30))
        shop = Shop(name="TZ", phone="1", address="x", created_at=datetime(2026, 1, 1, 12, 0, tzinfo=ist))
        session.add(shop)
        session.commit()

        stored = raw(session, f"SELECT created_at FROM shops WHERE id = {shop.id}")
        assert str(stored).startswith("2026-01-01 06:30:00")  # 12:00 IST is 06:30 UTC

        session.expire_all()
        loaded = session.get(Shop, shop.id)
        assert loaded.created_at == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
        assert loaded.created_at.tzinfo is UTC

    def test_naive_datetime_is_rejected(self, session: Session):
        with pytest.raises(Exception, match="Naive datetime"):
            session.add(Shop(name="Naive", phone="1", address="x", created_at=datetime(2026, 1, 1)))
            session.flush()
        session.rollback()
