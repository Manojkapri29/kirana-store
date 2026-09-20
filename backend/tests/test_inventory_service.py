"""inventory_service: opening stock, adjustments, derived stock, status, history. Service-level tests."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import AuditLog, InventoryTransaction
from app.models.enums import AdjustmentReason, InventoryTxnType, StockReferenceType
from app.services import inventory_service as inv
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.inventory_service import StockStatus, stock_status
from app.services.shop_service import get_shop, shop_today
from tests import factories
from tests.conftest import assert_sql_rejected, context_for


@pytest.fixture
def ctx(tenant_a):
    return context_for(tenant_a)


@pytest.fixture
def rice(session, tenant_a):
    """A product counted in pieces."""
    product = factories.make_product(session, tenant_a.shop, tenant_a.category, sku="RICE")
    session.commit()
    return product


@pytest.fixture
def sugar(session, tenant_a, units):
    """A product counted in kilograms (fractions allowed)."""
    product = factories.make_product(
        session, tenant_a.shop, tenant_a.category, sku="SUGAR", unit_id=units["kg"]
    )
    session.commit()
    return product


class TestOpeningStock:
    def test_creates_one_opening_ledger_row(self, session, tenant_a, ctx, rice):
        row = inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        session.commit()

        stored = session.scalars(select(InventoryTransaction)).one()
        assert stored.id == row.id
        assert stored.txn_type is InventoryTxnType.OPENING
        assert stored.qty_delta == Decimal("20.000")
        assert stored.shop_id == tenant_a.shop.id and stored.product_id == rice.id
        assert (stored.reference_type, stored.reference_id) == (StockReferenceType.PRODUCT, rice.id)
        assert stored.created_by == tenant_a.user.id
        assert stored.txn_date == shop_today(get_shop(session, tenant_a.shop.id))

    def test_stock_is_derived_from_the_ledger_not_stored(self, session, tenant_a, ctx, rice):
        assert inv.get_stock(session, tenant_a.shop.id, rice.id) == Decimal("0.000")

        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        session.commit()

        assert inv.get_stock(session, tenant_a.shop.id, rice.id) == Decimal("20.000")
        assert "current_stock" not in type(rice).__table__.columns

    def test_the_cost_becomes_the_average_cost(self, session, ctx, rice):
        inv.record_opening_stock(
            session, ctx, product_id=rice.id, quantity=Decimal("10"), unit_cost=Decimal("42.50")
        )
        session.commit()
        session.refresh(rice)

        assert rice.avg_cost == Decimal("42.50")
        assert session.scalars(select(InventoryTransaction.unit_cost)).one() == Decimal("42.50")

    def test_an_unknown_cost_stays_null_and_is_never_zero(self, session, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("10"))
        session.commit()
        session.refresh(rice)

        assert rice.avg_cost is None
        assert session.scalars(select(InventoryTransaction.unit_cost)).one() is None

    def test_fractional_quantities_for_units_that_allow_them(self, session, tenant_a, ctx, sugar):
        inv.record_opening_stock(session, ctx, product_id=sugar.id, quantity=Decimal("2.5"))

        assert inv.get_stock(session, tenant_a.shop.id, sugar.id) == Decimal("2.500")

    def test_fractions_are_refused_for_units_that_cannot_be_split(self, session, ctx, rice):
        with pytest.raises(InvalidInputError, match="whole number") as caught:
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("2.5"))

        assert caught.value.field == "quantity"

    @pytest.mark.parametrize("quantity", ["0", "-1", "-0.001"])
    def test_quantity_must_be_greater_than_zero(self, session, ctx, rice, quantity):
        with pytest.raises(InvalidInputError, match="greater than zero"):
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal(quantity))

    def test_negative_cost_is_refused(self, session, ctx, rice):
        with pytest.raises(InvalidInputError, match="negative"):
            inv.record_opening_stock(
                session, ctx, product_id=rice.id, quantity=Decimal("1"), unit_cost=Decimal("-1")
            )

    def test_the_date_can_be_in_the_past_but_not_the_future(self, session, tenant_a, ctx, rice):
        today = shop_today(get_shop(session, tenant_a.shop.id))

        with pytest.raises(InvalidInputError, match="future"):
            inv.record_opening_stock(
                session, ctx, product_id=rice.id, quantity=Decimal("1"), txn_date=today + timedelta(days=1)
            )
        row = inv.record_opening_stock(
            session, ctx, product_id=rice.id, quantity=Decimal("1"), txn_date=today - timedelta(days=30)
        )
        assert row.txn_date == today - timedelta(days=30)

    def test_only_one_opening_row_per_product(self, session, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        session.commit()

        with pytest.raises(ConflictError, match="already been recorded"):
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("5"))

    def test_opening_must_come_before_any_other_movement(self, session, ctx, rice):
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("5"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
        )
        session.commit()

        with pytest.raises(ConflictError, match="before any other stock movement"):
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))

    def test_an_inactive_product_cannot_receive_opening_stock(self, session, ctx, rice):
        rice.is_active = False
        session.commit()

        with pytest.raises(ConflictError, match="inactive"):
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("1"))

    def test_it_is_recorded_in_the_audit_log(self, session, tenant_a, ctx, rice):
        inv.record_opening_stock(
            session, ctx, product_id=rice.id, quantity=Decimal("20"), unit_cost=Decimal("40")
        )
        session.commit()

        entry = session.scalars(select(AuditLog).where(AuditLog.action == "opening_stock")).one()
        assert entry.entity_id == rice.id and entry.user_id == tenant_a.user.id
        assert entry.after_json["stock"] == "20.000" and entry.after_json["avg_cost"] == "40.00"


class TestAdjustments:
    def test_multiple_opening_and_adjustment_transactions_give_the_right_balance(
        self, session, tenant_a, ctx, rice, sugar
    ):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        inv.record_opening_stock(session, ctx, product_id=sugar.id, quantity=Decimal("7.250"))
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("5"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
        )
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("-3"),
            reason_code=AdjustmentReason.DAMAGED,
        )
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("-1"),
            reason_code=AdjustmentReason.EXPIRED,
        )
        inv.record_adjustment(
            session,
            ctx,
            product_id=sugar.id,
            quantity_delta=Decimal("-0.25"),
            reason_code=AdjustmentReason.LOST,
        )
        session.commit()

        assert inv.get_stock(session, tenant_a.shop.id, rice.id) == Decimal("21.000")  # 20 + 5 - 3 - 1
        assert inv.get_stock(session, tenant_a.shop.id, sugar.id) == Decimal("7.000")  # 7.25 - 0.25
        assert inv.get_stock_map(session, tenant_a.shop.id, [rice.id, sugar.id]) == {
            rice.id: Decimal("21.000"),
            sugar.id: Decimal("7.000"),
        }

    def test_an_adjustment_carries_its_reason(self, session, ctx, rice):
        inv.record_adjustment(
            session, ctx, product_id=rice.id, quantity_delta=Decimal("2"),
            reason_code=AdjustmentReason.CUSTOMER_RETURN_NO_BILL, note="Returned by Ramesh",
        )  # fmt: skip
        session.commit()

        row = session.scalars(select(InventoryTransaction)).one()
        assert row.txn_type is InventoryTxnType.ADJUSTMENT
        assert (
            row.reason_code is AdjustmentReason.CUSTOMER_RETURN_NO_BILL and row.note == "Returned by Ramesh"
        )

    def test_other_needs_a_note_and_zero_is_refused(self, session, ctx, rice):
        with pytest.raises(InvalidInputError, match="describe"):
            inv.record_adjustment(
                session,
                ctx,
                product_id=rice.id,
                quantity_delta=Decimal("1"),
                reason_code=AdjustmentReason.OTHER,
                note="  ",
            )
        with pytest.raises(InvalidInputError, match="zero"):
            inv.record_adjustment(
                session,
                ctx,
                product_id=rice.id,
                quantity_delta=Decimal("0"),
                reason_code=AdjustmentReason.LOST,
            )

    def test_fractions_follow_the_unit_rule(self, session, ctx, rice):
        with pytest.raises(InvalidInputError, match="whole number"):
            inv.record_adjustment(
                session,
                ctx,
                product_id=rice.id,
                quantity_delta=Decimal("1.5"),
                reason_code=AdjustmentReason.LOST,
            )


class TestNegativeStock:
    def test_removing_more_than_is_available_is_refused_by_default(self, session, tenant_a, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        session.commit()

        with pytest.raises(ConflictError, match="Not enough stock"):
            inv.record_adjustment(
                session,
                ctx,
                product_id=rice.id,
                quantity_delta=Decimal("-21"),
                reason_code=AdjustmentReason.LOST,
            )
        assert inv.get_stock(session, tenant_a.shop.id, rice.id) == Decimal("20.000")  # nothing was written

    def test_removing_exactly_everything_is_allowed(self, session, tenant_a, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        inv.record_adjustment(
            session, ctx, product_id=rice.id, quantity_delta=Decimal("-20"), reason_code=AdjustmentReason.LOST
        )
        session.commit()

        assert inv.get_stock(session, tenant_a.shop.id, rice.id) == Decimal("0.000")

    def test_a_shop_that_allows_negative_stock_may_go_below_zero(self, session, tenant_a, ctx, rice):
        tenant_a.shop.allow_negative_stock = True
        session.commit()
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))

        inv.record_adjustment(
            session, ctx, product_id=rice.id, quantity_delta=Decimal("-30"), reason_code=AdjustmentReason.LOST
        )
        session.commit()

        stock = inv.get_stock(session, tenant_a.shop.id, rice.id)
        assert stock == Decimal("-10.000") and stock_status(stock, Decimal("5")) is StockStatus.OUT_OF_STOCK

    def test_opening_stock_itself_can_never_be_negative_even_when_the_shop_allows_it(
        self, session, tenant_a, ctx, rice
    ):
        tenant_a.shop.allow_negative_stock = True
        session.commit()

        with pytest.raises(InvalidInputError, match="greater than zero"):
            inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("-5"))

    def test_the_setting_is_per_shop(self, session, tenant_a, tenant_b, rice):
        tenant_b.shop.allow_negative_stock = True
        session.commit()
        inv.record_opening_stock(session, context_for(tenant_a), product_id=rice.id, quantity=Decimal("1"))
        session.commit()

        with pytest.raises(ConflictError):  # Shop A still refuses, whatever Shop B allows
            inv.record_adjustment(
                session,
                context_for(tenant_a),
                product_id=rice.id,
                quantity_delta=Decimal("-2"),
                reason_code=AdjustmentReason.LOST,
            )


class TestStockStatus:
    @pytest.mark.parametrize(
        ("stock", "reorder", "expected"),
        [
            ("0", "5", StockStatus.OUT_OF_STOCK),
            ("0", "0", StockStatus.OUT_OF_STOCK),
            ("-3", "0", StockStatus.OUT_OF_STOCK),
            ("0.001", "5", StockStatus.LOW_STOCK),
            ("4.999", "5", StockStatus.LOW_STOCK),
            ("5", "5", StockStatus.LOW_STOCK),  # at the reorder level counts as low
            ("5.001", "5", StockStatus.IN_STOCK),
            ("10", "0", StockStatus.IN_STOCK),
            ("1", "0", StockStatus.IN_STOCK),
        ],
    )
    def test_status_rules(self, stock, reorder, expected):
        assert stock_status(Decimal(stock), Decimal(reorder)) is expected


class TestShopIsolation:
    def test_another_shops_product_is_not_found(self, session, tenant_a, tenant_b, rice):
        with pytest.raises(NotFoundError):
            inv.record_opening_stock(
                session, context_for(tenant_b), product_id=rice.id, quantity=Decimal("1")
            )
        with pytest.raises(NotFoundError):
            inv.record_adjustment(
                session,
                context_for(tenant_b),
                product_id=rice.id,
                quantity_delta=Decimal("1"),
                reason_code=AdjustmentReason.LOST,
            )

    def test_stock_history_and_inventory_are_scoped_to_the_shop(self, session, tenant_a, tenant_b, rice):
        theirs = factories.make_product(session, tenant_b.shop, tenant_b.category, sku="THEIRS")
        session.commit()
        inv.record_opening_stock(session, context_for(tenant_a), product_id=rice.id, quantity=Decimal("20"))
        inv.record_opening_stock(session, context_for(tenant_b), product_id=theirs.id, quantity=Decimal("99"))
        session.commit()

        rows_a, total_a = inv.list_inventory(session, tenant_a.shop.id)
        history_a, _ = inv.list_transactions(session, tenant_a.shop.id)
        history_a_for_theirs, _ = inv.list_transactions(session, tenant_a.shop.id, product_id=theirs.id)

        assert [(r.sku, r.current_stock) for r in rows_a] == [("RICE", Decimal("20.000"))] and total_a == 1
        assert [t.sku for t in history_a] == ["RICE"]
        assert history_a_for_theirs == []
        assert inv.get_stock(session, tenant_a.shop.id, theirs.id) == Decimal("0.000")  # not Shop B's 99
        assert inv.get_stock_map(session, tenant_a.shop.id, [theirs.id]) == {theirs.id: Decimal("0.000")}


class TestInsertOnly:
    def test_the_service_offers_no_way_to_change_or_remove_ledger_rows(self):
        public = [name for name in dir(inv) if not name.startswith("_")]

        assert [
            n for n in public if n.startswith(("update", "delete", "remove", "edit", "set_", "overwrite"))
        ] == []

    def test_a_row_written_by_the_service_cannot_be_changed_or_deleted(self, session, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        session.commit()

        assert_sql_rejected(
            session, "UPDATE inventory_transactions SET qty_delta = 999000", match="insert-only"
        )
        assert_sql_rejected(session, "DELETE FROM inventory_transactions", match="insert-only")
        assert session.scalar(text("SELECT qty_delta FROM inventory_transactions")) == 20000


class TestListingAndHistory:
    @pytest.fixture
    def stocked(self, session, tenant_a, ctx, units):
        """Four products: in stock, low, out, and one inactive."""
        make = lambda sku, name, **kw: factories.make_product(  # noqa: E731
            session, tenant_a.shop, tenant_a.category, sku=sku, name=name, **kw
        )
        products = {
            "ok": make("OK", "Atta", reorder_level=Decimal("5")),
            "low": make("LOW", "Besan", reorder_level=Decimal("5")),
            "out": make("OUT", "Chana", reorder_level=Decimal("5")),
            "old": make("OLD", "Dal", reorder_level=Decimal("5")),
        }
        session.commit()
        inv.record_opening_stock(session, ctx, product_id=products["ok"].id, quantity=Decimal("50"))
        inv.record_opening_stock(session, ctx, product_id=products["low"].id, quantity=Decimal("3"))
        inv.record_opening_stock(session, ctx, product_id=products["old"].id, quantity=Decimal("9"))
        products["old"].is_active = False
        session.commit()
        return products

    def test_inventory_shows_status_from_the_reorder_level(self, session, tenant_a, stocked):
        rows, total = inv.list_inventory(session, tenant_a.shop.id)

        assert total == 3  # inactive hidden by default
        assert [(r.sku, r.current_stock, r.status) for r in rows] == [
            ("OK", Decimal("50.000"), StockStatus.IN_STOCK),
            ("LOW", Decimal("3.000"), StockStatus.LOW_STOCK),
            ("OUT", Decimal("0.000"), StockStatus.OUT_OF_STOCK),
        ]

    @pytest.mark.parametrize(
        ("wanted", "skus"),
        [
            (StockStatus.IN_STOCK, ["OK"]),
            (StockStatus.LOW_STOCK, ["LOW"]),
            (StockStatus.OUT_OF_STOCK, ["OUT"]),
        ],
    )
    def test_filtering_by_stock_status(self, session, tenant_a, stocked, wanted, skus):
        rows, total = inv.list_inventory(session, tenant_a.shop.id, status=wanted)

        assert [r.sku for r in rows] == skus and total == 1

    def test_inactive_and_all_and_search_and_paging(self, session, tenant_a, stocked):
        assert [r.sku for r in inv.list_inventory(session, tenant_a.shop.id, active=False)[0]] == ["OLD"]
        assert inv.list_inventory(session, tenant_a.shop.id, active=None)[1] == 4
        assert [r.sku for r in inv.list_inventory(session, tenant_a.shop.id, q="besan")[0]] == ["LOW"]
        rows, total = inv.list_inventory(session, tenant_a.shop.id, limit=1, offset=1)
        assert (len(rows), total) == (1, 3) and rows[0].sku == "LOW"

    def test_history_has_a_running_balance_and_newest_first(self, session, tenant_a, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("5"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
        )
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("-3"),
            reason_code=AdjustmentReason.DAMAGED,
        )
        session.commit()

        rows, total = inv.list_transactions(session, tenant_a.shop.id, product_id=rice.id)

        assert total == 3
        assert [(r.txn_type.value, r.qty_delta, r.balance_after) for r in rows] == [
            ("ADJUSTMENT", Decimal("-3.000"), Decimal("22.000")),
            ("ADJUSTMENT", Decimal("5.000"), Decimal("25.000")),
            ("OPENING", Decimal("20.000"), Decimal("20.000")),
        ]
        assert rows[0].created_by_name == "Test Owner" and rows[0].reason_code is AdjustmentReason.DAMAGED

    def test_the_running_balance_is_not_disturbed_by_filters_or_paging(self, session, tenant_a, ctx, rice):
        inv.record_opening_stock(session, ctx, product_id=rice.id, quantity=Decimal("20"))
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("5"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
        )
        session.commit()

        only_adjustments, _ = inv.list_transactions(
            session, tenant_a.shop.id, txn_type=InventoryTxnType.ADJUSTMENT
        )
        second_page, total = inv.list_transactions(session, tenant_a.shop.id, limit=1, offset=1)

        assert only_adjustments[0].balance_after == Decimal("25.000")  # includes the opening 20
        assert (second_page[0].txn_type, second_page[0].balance_after, total) == (
            InventoryTxnType.OPENING,
            Decimal("20.000"),
            2,
        )

    def test_date_range_and_oldest_first(self, session, tenant_a, ctx, rice):
        today = shop_today(get_shop(session, tenant_a.shop.id))
        inv.record_opening_stock(
            session, ctx, product_id=rice.id, quantity=Decimal("20"), txn_date=today - timedelta(days=10)
        )
        inv.record_adjustment(
            session,
            ctx,
            product_id=rice.id,
            quantity_delta=Decimal("1"),
            reason_code=AdjustmentReason.COUNT_CORRECTION,
            txn_date=today,
        )
        session.commit()

        recent, _ = inv.list_transactions(session, tenant_a.shop.id, date_from=today - timedelta(days=1))
        oldest_first, _ = inv.list_transactions(session, tenant_a.shop.id, newest_first=False)

        assert [r.txn_type.value for r in recent] == ["ADJUSTMENT"]
        assert [r.txn_type.value for r in oldest_first] == ["OPENING", "ADJUSTMENT"]
