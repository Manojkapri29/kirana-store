"""The two insert-only ledgers: stock (inventory_transactions) and khata (customer_ledger).

These tests write ledger rows directly, because the services that will normally do so (inventory_service,
khata_service) arrive in later phases. They check the rules the DATABASE enforces regardless of who writes.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import AuditLog, CustomerLedgerEntry, InventoryTransaction
from app.models.enums import (
    AdjustmentReason,
    CustomerLedgerEntryType,
    InventoryTxnType,
    KhataReferenceType,
    PaymentMethod,
    StockReferenceType,
)
from tests import factories
from tests.conftest import Tenant, assert_rejected, assert_sql_rejected
from tests.factories import TODAY

T = InventoryTxnType
E = CustomerLedgerEntryType


def txn(tenant: Tenant, product, txn_type: InventoryTxnType, qty: str, **overrides) -> InventoryTransaction:
    fields = {
        "shop_id": tenant.shop.id, "product_id": product.id, "txn_type": txn_type,
        "qty_delta": Decimal(qty), "txn_date": TODAY, "created_by": tenant.user.id,
    }  # fmt: skip
    if txn_type is T.ADJUSTMENT:
        fields.setdefault("reason_code", AdjustmentReason.COUNT_CORRECTION)
    fields.update(overrides)
    return InventoryTransaction(**fields)


@pytest.fixture
def product(session, tenant_a):
    product = factories.make_product(session, tenant_a.shop, tenant_a.category)
    session.commit()  # committed, so a rolled-back failed insert in a test cannot discard it
    return product


class TestStockLedgerSigns:
    @pytest.mark.parametrize(
        ("txn_type", "qty"),
        [
            (T.OPENING, "20"), (T.PURCHASE, "30"), (T.SALE, "-5"), (T.SALE_RETURN, "2"),
            (T.PURCHASE_RETURN, "-1"), (T.ADJUSTMENT, "-3"), (T.ADJUSTMENT, "3"), (T.OPENING, "0.250"),
        ],
    )  # fmt: skip
    def test_valid_movements_are_accepted(self, session, tenant_a, product, txn_type, qty):
        session.add(txn(tenant_a, product, txn_type, qty))
        session.commit()

    @pytest.mark.parametrize(
        ("txn_type", "qty"),
        [
            (T.OPENING, "-1"), (T.OPENING, "0"), (T.PURCHASE, "-5"), (T.PURCHASE, "0"), (T.SALE, "5"),
            (T.SALE, "0"), (T.SALE_RETURN, "-1"), (T.PURCHASE_RETURN, "1"), (T.ADJUSTMENT, "0"),
        ],
    )  # fmt: skip
    def test_a_sign_that_contradicts_the_type_is_rejected(self, session, tenant_a, product, txn_type, qty):
        assert_rejected(session, txn(tenant_a, product, txn_type, qty), match="sign_matches_type")

    def test_every_transaction_type_from_the_spec_exists(self):
        assert {t.value for t in T} == {
            "OPENING", "PURCHASE", "SALE", "SALE_RETURN", "PURCHASE_RETURN", "ADJUSTMENT", "REVERSAL",
        }  # fmt: skip

    def test_unknown_cost_is_null_and_cost_cannot_be_negative(self, session, tenant_a, product):
        session.add(txn(tenant_a, product, T.OPENING, "10"))
        session.commit()
        assert session.scalar(select(InventoryTransaction.unit_cost)) is None

        assert_rejected(
            session,
            txn(tenant_a, product, T.PURCHASE, "1", unit_cost=Decimal("-1")),
            match="unit_cost_non_negative",
        )

    def test_the_ledger_cannot_reference_another_shops_product_or_user(
        self, session, tenant_a, tenant_b, product
    ):
        theirs = factories.make_product(session, tenant_b.shop, tenant_b.category)
        session.commit()

        assert_rejected(session, txn(tenant_a, theirs, T.OPENING, "1"), match="FOREIGN KEY")
        assert_rejected(
            session, txn(tenant_a, product, T.OPENING, "1", created_by=tenant_b.user.id), match="FOREIGN KEY"
        )


class TestAdjustmentReasons:
    def test_every_documented_reason_code_is_accepted(self, session, tenant_a, product):
        assert {r.value for r in AdjustmentReason} == {
            "CUSTOMER_RETURN_NO_BILL", "COUNT_CORRECTION", "DAMAGED", "EXPIRED", "LOST", "OTHER",
        }  # fmt: skip
        for reason in AdjustmentReason:
            session.add(txn(tenant_a, product, T.ADJUSTMENT, "-1", reason_code=reason, note="see note"))
        session.commit()

    def test_an_adjustment_without_a_reason_is_rejected(self, session, tenant_a, product):
        assert_rejected(
            session,
            txn(tenant_a, product, T.ADJUSTMENT, "-1", reason_code=None),
            match="reason_only_for_adjustment",
        )

    def test_a_reason_on_a_non_adjustment_is_rejected(self, session, tenant_a, product):
        assert_rejected(
            session,
            txn(tenant_a, product, T.PURCHASE, "5", reason_code=AdjustmentReason.DAMAGED),
            match="reason_only_for_adjustment",
        )

    @pytest.mark.parametrize("note", [None, "", "   "])
    def test_other_requires_a_real_note(self, session, tenant_a, product, note):
        assert_rejected(
            session,
            txn(tenant_a, product, T.ADJUSTMENT, "-1", reason_code=AdjustmentReason.OTHER, note=note),
            match="other_reason_needs_note",
        )

    RAW_ADJUSTMENT = (
        "INSERT INTO inventory_transactions"
        " (shop_id, product_id, txn_type, qty_delta, txn_date, reason_code, created_by, created_at)"
        " VALUES (:shop, :product, 'ADJUSTMENT', -1000, '2026-09-20', :reason, :user, '2026-09-20')"
    )

    def test_a_generic_reason_string_is_not_accepted(self, session, tenant_a, product):
        """The database itself rejects any reason outside the documented list (here via raw SQL)."""
        params = {"shop": tenant_a.shop.id, "product": product.id, "user": tenant_a.user.id}

        assert_sql_rejected(
            session,
            self.RAW_ADJUSTMENT,
            {**params, "reason": "MISC"},
            match="ck_inventory_transactions_reason_code",
        )
        # Control: the same statement with a documented reason is accepted, so the rejection above is
        # really about the reason and not about any other column.
        session.execute(text(self.RAW_ADJUSTMENT), {**params, "reason": "LOST"})
        session.commit()


class TestReversalsAndReferences:
    def make_sale_row(self, session, tenant, product) -> InventoryTransaction:
        row = txn(
            tenant, product, T.SALE, "-5", reference_type=StockReferenceType.SALE_ITEM, reference_id=101
        )
        session.add(row)
        session.commit()
        return row

    def test_a_reversal_points_at_the_row_it_undoes(self, session, tenant_a, product):
        original = self.make_sale_row(session, tenant_a, product)

        session.add(txn(tenant_a, product, T.REVERSAL, "5", reverses_txn_id=original.id))
        session.commit()

    def test_a_reversal_without_an_original_is_rejected(self, session, tenant_a, product):
        assert_rejected(session, txn(tenant_a, product, T.REVERSAL, "5"), match="reversal_links_original")

    def test_only_reversals_may_point_at_an_original(self, session, tenant_a, product):
        original = self.make_sale_row(session, tenant_a, product)

        assert_rejected(
            session,
            txn(tenant_a, product, T.PURCHASE, "5", reverses_txn_id=original.id),
            match="reversal_links_original",
        )

    def test_a_row_can_be_reversed_only_once(self, session, tenant_a, product):
        original = self.make_sale_row(session, tenant_a, product)
        session.add(txn(tenant_a, product, T.REVERSAL, "5", reverses_txn_id=original.id))
        session.commit()

        assert_rejected(
            session, txn(tenant_a, product, T.REVERSAL, "5", reverses_txn_id=original.id), match="UNIQUE"
        )

    def test_a_reversal_cannot_target_another_shops_row(self, session, tenant_a, tenant_b, product):
        theirs_product = factories.make_product(session, tenant_b.shop, tenant_b.category)
        theirs = self.make_sale_row(session, tenant_b, theirs_product)

        assert_rejected(
            session, txn(tenant_a, product, T.REVERSAL, "5", reverses_txn_id=theirs.id), match="FOREIGN KEY"
        )

    def test_a_document_line_cannot_post_the_same_transaction_twice(self, session, tenant_a, product):
        self.make_sale_row(session, tenant_a, product)

        assert_rejected(
            session,
            txn(
                tenant_a, product, T.SALE, "-5", reference_type=StockReferenceType.SALE_ITEM, reference_id=101
            ),
            match="UNIQUE",
        )

    def test_reference_type_and_id_travel_together(self, session, tenant_a, product):
        assert_rejected(
            session,
            txn(tenant_a, product, T.OPENING, "1", reference_type=StockReferenceType.PRODUCT),
            match="reference_pair",
        )
        assert_rejected(
            session, txn(tenant_a, product, T.OPENING, "1", reference_id=5), match="reference_pair"
        )

    def test_adjustments_need_no_source_document(self, session, tenant_a, product):
        session.add_all(
            [txn(tenant_a, product, T.ADJUSTMENT, "-1", reason_code=AdjustmentReason.LOST) for _ in range(2)]
        )
        session.commit()


class TestStockIsDerivedFromTheLedger:
    def test_current_stock_is_the_sum_of_signed_movements(self, session, tenant_a, product):
        other = factories.make_product(session, tenant_a.shop, tenant_a.category, sku="OTHER")
        session.add_all(
            [
                txn(tenant_a, product, T.OPENING, "20"),
                txn(tenant_a, product, T.PURCHASE, "30"),
                txn(tenant_a, product, T.SALE, "-5"),
                txn(tenant_a, product, T.SALE, "-3"),
                txn(tenant_a, product, T.SALE_RETURN, "1"),
                txn(tenant_a, product, T.PURCHASE_RETURN, "-2"),
                txn(tenant_a, product, T.ADJUSTMENT, "-0.5", reason_code=AdjustmentReason.DAMAGED),
                txn(tenant_a, other, T.OPENING, "7.250"),
            ]
        )
        session.commit()

        stock = dict(
            session.execute(
                select(InventoryTransaction.product_id, func.sum(InventoryTransaction.qty_delta)).group_by(
                    InventoryTransaction.product_id
                )
            ).all()
        )

        assert stock[product.id] == Decimal("40.500")  # 20 + 30 - 5 - 3 + 1 - 2 - 0.5
        assert stock[other.id] == Decimal("7.250")

    def test_the_spec_example_opening_20_plus_purchase_30_minus_sale_5(self, session, tenant_a, product):
        session.add_all(
            [
                txn(tenant_a, product, T.OPENING, "20"),
                txn(tenant_a, product, T.PURCHASE, "30"),
                txn(tenant_a, product, T.SALE, "-5"),
            ]
        )
        session.commit()

        assert session.scalar(select(func.sum(InventoryTransaction.qty_delta))) == Decimal("45.000")

    def test_a_reversal_cancels_the_original_in_the_sum(self, session, tenant_a, product):
        original = txn(tenant_a, product, T.SALE, "-5")
        session.add_all([txn(tenant_a, product, T.OPENING, "20"), original])
        session.commit()
        session.add(txn(tenant_a, product, T.REVERSAL, "5", reverses_txn_id=original.id))
        session.commit()

        assert session.scalar(select(func.sum(InventoryTransaction.qty_delta))) == Decimal("20.000")


class TestInsertOnly:
    @pytest.fixture
    def stock_row(self, session, tenant_a, product) -> InventoryTransaction:
        row = txn(tenant_a, product, T.OPENING, "10")
        session.add(row)
        session.commit()
        return row

    @pytest.fixture
    def khata_row(self, session, tenant_a) -> CustomerLedgerEntry:
        customer = factories.make_customer(session, tenant_a.shop)
        row = entry(tenant_a, customer, E.OPENING_BALANCE, "250.00")
        session.add(row)
        session.commit()
        return row

    @pytest.fixture
    def audit_row(self, session, tenant_a) -> AuditLog:
        row = AuditLog(shop_id=tenant_a.shop.id, entity_type="product", action="create")
        session.add(row)
        session.commit()
        return row

    @pytest.mark.parametrize(
        ("table", "fixture_name", "update_sql"),
        [
            ("inventory_transactions", "stock_row", "SET note = 'edited'"),
            ("customer_ledger", "khata_row", "SET note = 'edited'"),
            ("audit_log", "audit_row", "SET action = 'edited'"),
        ],
    )
    def test_rows_cannot_be_updated_or_deleted(self, request, session, table, fixture_name, update_sql):
        request.getfixturevalue(fixture_name)

        assert_sql_rejected(session, f"UPDATE {table} {update_sql}", match=f"{table} is insert-only")
        assert_sql_rejected(session, f"DELETE FROM {table}", match=f"{table} is insert-only")
        assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 1

    def test_changing_a_stock_quantity_through_the_orm_is_refused_too(self, session, stock_row):
        stock_row.qty_delta = Decimal("999")

        with pytest.raises(Exception, match="insert-only"):
            session.flush()
        session.rollback()

    def test_the_only_way_to_correct_a_row_is_a_new_row(self, session, tenant_a, product, stock_row):
        session.add(txn(tenant_a, product, T.REVERSAL, "-10", reverses_txn_id=stock_row.id))
        session.commit()

        assert session.scalar(text("SELECT count(*) FROM inventory_transactions")) == 2


def entry(
    tenant: Tenant, customer, entry_type: CustomerLedgerEntryType, amount: str, **overrides
) -> CustomerLedgerEntry:
    fields = {
        "shop_id": tenant.shop.id, "customer_id": customer.id, "entry_date": TODAY,
        "entry_type": entry_type, "amount_delta": Decimal(amount), "created_by": tenant.user.id,
    }  # fmt: skip
    fields.update(overrides)
    return CustomerLedgerEntry(**fields)


class TestCustomerLedger:
    @pytest.fixture
    def customer(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop, "Ramesh", phone="9876543210")
        session.commit()
        return customer

    def test_every_documented_entry_type_exists(self):
        assert {e.value for e in E} == {
            "OPENING_BALANCE", "CREDIT_SALE", "PAYMENT", "RETURN_CREDIT", "ADJUSTMENT", "REVERSAL",
        }  # fmt: skip

    @pytest.mark.parametrize(
        ("entry_type", "amount"),
        [
            (E.OPENING_BALANCE, "500.00"), (E.OPENING_BALANCE, "-50.00"), (E.CREDIT_SALE, "120.50"),
            (E.PAYMENT, "-100.00"), (E.RETURN_CREDIT, "-20.00"),
            (E.ADJUSTMENT, "-5.00"), (E.ADJUSTMENT, "5.00"),
        ],
    )  # fmt: skip
    def test_valid_entries(self, session, tenant_a, customer, entry_type, amount):
        session.add(entry(tenant_a, customer, entry_type, amount))
        session.commit()

    @pytest.mark.parametrize(
        ("entry_type", "amount"),
        [
            (E.CREDIT_SALE, "-10.00"), (E.CREDIT_SALE, "0"), (E.PAYMENT, "10.00"), (E.PAYMENT, "0"),
            (E.RETURN_CREDIT, "10.00"), (E.OPENING_BALANCE, "0"), (E.ADJUSTMENT, "0"),
        ],
    )  # fmt: skip
    def test_a_sign_that_contradicts_the_type_is_rejected(
        self, session, tenant_a, customer, entry_type, amount
    ):
        assert_rejected(session, entry(tenant_a, customer, entry_type, amount), match="sign_matches_type")

    def test_outstanding_balance_is_the_sum_of_the_ledger(self, session, tenant_a, customer):
        session.add_all(
            [
                entry(tenant_a, customer, E.OPENING_BALANCE, "500.00"),
                entry(
                    tenant_a,
                    customer,
                    E.CREDIT_SALE,
                    "120.50",
                    reference_type=KhataReferenceType.SALE,
                    reference_id=1,
                ),
                entry(tenant_a, customer, E.PAYMENT, "-200.00", payment_method=PaymentMethod.UPI),
                entry(
                    tenant_a,
                    customer,
                    E.RETURN_CREDIT,
                    "-20.25",
                    reference_type=KhataReferenceType.SALES_RETURN,
                    reference_id=1,
                ),
            ]
        )
        session.commit()

        balance = session.scalar(
            select(func.sum(CustomerLedgerEntry.amount_delta)).where(
                CustomerLedgerEntry.customer_id == customer.id
            )
        )

        assert balance == Decimal("400.25")  # the customer owes 400.25

    def test_reversal_rules(self, session, tenant_a, customer):
        credit = entry(tenant_a, customer, E.CREDIT_SALE, "100.00")
        session.add(credit)
        session.commit()

        assert_rejected(
            session, entry(tenant_a, customer, E.REVERSAL, "-100.00"), match="reversal_links_original"
        )
        session.add(entry(tenant_a, customer, E.REVERSAL, "-100.00", reverses_entry_id=credit.id))
        session.commit()
        assert_rejected(
            session,
            entry(tenant_a, customer, E.REVERSAL, "-100.00", reverses_entry_id=credit.id),
            match="UNIQUE",
        )

    def test_payment_method_is_only_for_payments(self, session, tenant_a, customer):
        assert_rejected(
            session,
            entry(tenant_a, customer, E.CREDIT_SALE, "10", payment_method=PaymentMethod.CASH),
            match="method_only_for_payment",
        )

    def test_ledger_cannot_reference_another_shops_customer(self, session, tenant_a, tenant_b):
        foreign = factories.make_customer(session, tenant_b.shop)
        session.commit()

        assert_rejected(session, entry(tenant_a, foreign, E.OPENING_BALANCE, "10"), match="FOREIGN KEY")

    def test_quick_sales_and_returns_can_be_referenced_as_sources(self):
        assert {r.value for r in KhataReferenceType} == {"SALE", "QUICK_SALE", "SALES_RETURN"}
