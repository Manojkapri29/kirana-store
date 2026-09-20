"""Database constraints: uniqueness, foreign keys, tenant isolation, validity of values."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError

from app.models import (
    AuditLog,
    Category,
    Customer,
    DocumentSequence,
    Expense,
    ExpenseCategory,
    IdempotencyKey,
    Product,
    Purchase,
    QuickSale,
    Sale,
    SaleItem,
    Shop,
    Supplier,
    User,
)
from app.models.enums import DocumentStatus, PaymentMethod, PaymentType
from tests import factories
from tests.conftest import Tenant, assert_rejected, assert_sql_rejected
from tests.factories import TODAY, make_product, product_kwargs


def new_product(session, tenant: Tenant, **overrides) -> Product:
    return Product(**product_kwargs(tenant.shop, tenant.category, session, **overrides))


class TestSkuUniqueness:
    def test_two_shops_can_use_the_same_sku(self, session, tenant_a, tenant_b):
        make_product(session, tenant_a.shop, tenant_a.category, sku="RICE-5KG")
        make_product(session, tenant_b.shop, tenant_b.category, sku="RICE-5KG")
        session.commit()  # no error

    def test_the_same_shop_cannot_repeat_a_sku(self, session, tenant_a):
        make_product(session, tenant_a.shop, tenant_a.category, sku="RICE-5KG")
        session.commit()

        assert_rejected(session, new_product(session, tenant_a, sku="RICE-5KG", name="Other"), match="UNIQUE")

    def test_a_blank_sku_is_rejected(self, session, tenant_a):
        assert_rejected(session, new_product(session, tenant_a, sku="   "), match="sku_not_blank")


class TestBarcodeUniqueness:
    def test_two_shops_can_use_the_same_barcode(self, session, tenant_a, tenant_b):
        make_product(session, tenant_a.shop, tenant_a.category, sku="A", barcode="8901234567890")
        make_product(session, tenant_b.shop, tenant_b.category, sku="A", barcode="8901234567890")
        session.commit()

    def test_the_same_shop_cannot_repeat_a_barcode(self, session, tenant_a):
        make_product(session, tenant_a.shop, tenant_a.category, sku="A", barcode="8901234567890")
        session.commit()

        assert_rejected(
            session, new_product(session, tenant_a, sku="B", barcode="8901234567890"), match="UNIQUE"
        )

    def test_barcode_is_optional_and_many_products_may_have_none(self, session, tenant_a):
        for sku in ("A", "B", "C"):
            make_product(session, tenant_a.shop, tenant_a.category, sku=sku, barcode=None)
        session.commit()

        assert session.query(Product).filter(Product.barcode.is_(None)).count() == 3

    def test_an_empty_barcode_is_rejected_so_it_cannot_collide_silently(self, session, tenant_a):
        assert_rejected(session, new_product(session, tenant_a, barcode=""), match="barcode_not_blank")


class TestForeignKeys:
    def test_product_needs_an_existing_category(self, session, tenant_a):
        assert_rejected(session, new_product(session, tenant_a, category_id=99999), match="FOREIGN KEY")

    def test_product_needs_an_existing_unit(self, session, tenant_a):
        assert_rejected(session, new_product(session, tenant_a, unit_id=99999), match="FOREIGN KEY")

    def test_a_shop_cannot_be_deleted_while_it_owns_data(self, session, tenant_a):
        with pytest.raises(Exception, match="FOREIGN KEY"):
            session.execute(Shop.__table__.delete().where(Shop.id == tenant_a.shop.id))
        session.rollback()

    def test_customer_must_belong_to_an_existing_shop(self, session):
        assert_rejected(session, Customer(shop_id=99999, name="Ghost"), match="FOREIGN KEY")


class TestShopIsolation:
    """A row of Shop A must never point at a row of Shop B (composite foreign keys)."""

    def test_product_cannot_use_another_shops_category(self, session, tenant_a, tenant_b):
        assert_rejected(
            session,
            Product(**product_kwargs(tenant_a.shop, tenant_b.category, session)),
            match="FOREIGN KEY",
        )

    def test_product_cannot_use_another_shops_default_supplier(self, session, tenant_a, tenant_b):
        foreign_supplier = factories.make_supplier(session, tenant_b.shop)
        session.commit()

        assert_rejected(
            session,
            new_product(session, tenant_a, default_supplier_id=foreign_supplier.id),
            match="FOREIGN KEY",
        )

    def test_product_can_use_its_own_shops_supplier_and_none(self, session, tenant_a):
        own = factories.make_supplier(session, tenant_a.shop)
        make_product(session, tenant_a.shop, tenant_a.category, sku="A", default_supplier_id=own.id)
        make_product(session, tenant_a.shop, tenant_a.category, sku="B", default_supplier_id=None)
        session.commit()

    def test_sale_cannot_use_another_shops_customer(self, session, tenant_a, tenant_b):
        foreign_customer = factories.make_customer(session, tenant_b.shop)
        session.commit()

        assert_rejected(session, cash_sale(tenant_a, customer_id=foreign_customer.id), match="FOREIGN KEY")

    def test_sale_line_cannot_use_another_shops_product(self, session, tenant_a, tenant_b):
        sale = cash_sale(tenant_a)
        foreign_product = make_product(session, tenant_b.shop, tenant_b.category)
        session.add(sale)
        session.commit()

        assert_rejected(
            session,
            SaleItem(
                shop_id=tenant_a.shop.id,
                sale_id=sale.id,
                product_id=foreign_product.id,
                quantity=Decimal("1"),
                unit_price=Decimal("10"),
                line_total=Decimal("10"),
            ),  # fmt: skip
            match="FOREIGN KEY",
        )

    def test_document_cannot_be_attributed_to_another_shops_user(self, session, tenant_a, tenant_b):
        assert_rejected(session, cash_sale(tenant_a, created_by=tenant_b.user.id), match="FOREIGN KEY")

    def test_expense_cannot_use_another_shops_category(self, session, tenant_a, tenant_b):
        foreign = ExpenseCategory(shop_id=tenant_b.shop.id, name="Rent")
        session.add(foreign)
        session.commit()

        assert_rejected(
            session,
            Expense(
                shop_id=tenant_a.shop.id,
                expense_date=TODAY,
                category_id=foreign.id,
                amount=Decimal("500"),
                payment_method=PaymentMethod.CASH,
                created_by=tenant_a.user.id,
            ),  # fmt: skip
            match="FOREIGN KEY",
        )

    def test_names_only_need_to_be_unique_within_a_shop(self, session, tenant_a, tenant_b):
        factories.make_category(session, tenant_a.shop, "Dairy")
        factories.make_category(session, tenant_b.shop, "Dairy")
        session.commit()

        assert_rejected(session, Category(shop_id=tenant_a.shop.id, name="Dairy"), match="UNIQUE")


class TestCustomerPhone:
    def test_same_phone_in_two_shops_is_fine(self, session, tenant_a, tenant_b):
        factories.make_customer(session, tenant_a.shop, "Ramesh", phone="9876543210")
        factories.make_customer(session, tenant_b.shop, "Ramesh", phone="9876543210")
        session.commit()

    def test_same_phone_twice_in_one_shop_is_rejected(self, session, tenant_a):
        factories.make_customer(session, tenant_a.shop, "Ramesh", phone="9876543210")
        session.commit()

        assert_rejected(
            session, Customer(shop_id=tenant_a.shop.id, name="Suresh", phone="9876543210"), match="UNIQUE"
        )

    def test_phone_is_optional_and_many_customers_may_have_none(self, session, tenant_a):
        for name in ("A", "B", "C"):
            factories.make_customer(session, tenant_a.shop, name, phone=None)
        session.commit()

    def test_an_empty_phone_is_rejected(self, session, tenant_a):
        assert_rejected(
            session, Customer(shop_id=tenant_a.shop.id, name="X", phone=""), match="phone_not_blank"
        )


class TestValuesMustBeValid:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("selling_price", Decimal("-0.01")),
            ("purchase_price", Decimal("-1")),
            ("avg_cost", Decimal("-1")),
            ("mrp", Decimal("-1")),
            ("reorder_level", Decimal("-1")),
        ],
    )
    def test_negative_prices_and_levels_are_rejected(self, session, tenant_a, field, value):
        assert_rejected(session, new_product(session, tenant_a, **{field: value}), match="non_negative")

    def test_zero_is_a_valid_price(self, session, tenant_a):
        make_product(session, tenant_a.shop, tenant_a.category, selling_price=Decimal("0"))
        session.commit()

    def test_selling_price_above_mrp_is_allowed_by_the_database(self, session, tenant_a):
        """MRP validation is a per-shop setting (warn or block), so it is a service rule, not a CHECK."""
        make_product(
            session, tenant_a.shop, tenant_a.category, mrp=Decimal("100"), selling_price=Decimal("120")
        )
        session.commit()

    @pytest.mark.parametrize("field", ["name", "sku", "selling_price", "unit_id", "category_id"])
    def test_required_product_fields(self, session, tenant_a, field):
        assert_rejected(session, new_product(session, tenant_a, **{field: None}), match="NOT NULL")

    def test_blank_names_are_rejected(self, session, tenant_a):
        assert_rejected(session, new_product(session, tenant_a, name="   "), match="name_not_blank")
        assert_rejected(
            session, Shop(name="", business_type="OTHER", phone="1", address="x"), match="name_not_blank"
        )
        assert_rejected(session, Supplier(shop_id=tenant_a.shop.id, name=" "), match="name_not_blank")

    def test_invalid_enum_values_are_refused_by_the_database(self, session, tenant_a):
        assert_sql_rejected(
            session,
            "INSERT INTO users (shop_id, email, password_hash, full_name, role, is_active,"
            " created_at, updated_at)"
            " VALUES (:shop, 'x@test.local', '!', 'X', 'ADMIN', 1, '2026-01-01', '2026-01-01')",
            {"shop": tenant_a.shop.id},
            match="role",
        )

    def test_invalid_enum_values_are_also_caught_before_reaching_the_database(self, session, tenant_a):
        session.add(Shop(name="S", business_type="OTHER", phone="1", address="x", language="fr"))
        with pytest.raises(StatementError, match="not among the defined enum values"):
            session.flush()
        session.rollback()

    def test_emails_must_be_lowercase_and_unique(self, session, tenant_a):
        assert_rejected(
            session,
            User(shop_id=tenant_a.shop.id, email="Owner@Test.Local", password_hash="!", full_name="X"),
            match="email_lowercase",
        )
        assert_rejected(
            session,
            User(shop_id=tenant_a.shop.id, email=tenant_a.user.email, password_hash="!", full_name="X"),
            match="UNIQUE",
        )


def cash_sale(tenant: Tenant, **overrides) -> Sale:
    fields = {
        "shop_id": tenant.shop.id, "invoice_no": "INV-1", "sale_date": TODAY,
        "total_amount": Decimal("100.00"), "payment_type": PaymentType.PAID,
        "amount_paid": Decimal("100.00"), "payment_method": PaymentMethod.CASH,
        "created_by": tenant.user.id,
    }  # fmt: skip
    fields.update(overrides)
    return Sale(**fields)


def quick_sale(tenant: Tenant, **overrides) -> QuickSale:
    fields = {
        "shop_id": tenant.shop.id, "sale_date": TODAY, "total_amount": Decimal("5000.00"),
        "payment_type": PaymentType.PAID, "amount_paid": Decimal("5000.00"),
        "payment_method": PaymentMethod.CASH, "created_by": tenant.user.id,
    }  # fmt: skip
    fields.update(overrides)
    return QuickSale(**fields)


class TestSalePaymentRules:
    """Shared by Detailed Sales and Quick Sales."""

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_a_fully_paid_sale_is_valid(self, session, tenant_a, build):
        session.add(build(tenant_a))
        session.commit()

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_paid_means_the_full_amount_was_received(self, session, tenant_a, build):
        assert_rejected(session, build(tenant_a, amount_paid=Decimal("1.00")), match="paid_means_fully_paid")

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_a_credit_sale_needs_a_customer(self, session, tenant_a, build):
        sale = build(tenant_a, payment_type=PaymentType.CREDIT, amount_paid=Decimal("0"), payment_method=None)
        assert_rejected(session, sale, match="credit_needs_customer_and_balance")

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_a_credit_sale_must_leave_something_owed(self, session, tenant_a, build):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        sale = build(tenant_a, payment_type=PaymentType.CREDIT, customer_id=customer.id)  # paid in full

        assert_rejected(session, sale, match="credit_needs_customer_and_balance")

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_credit_sale_with_customer_and_part_payment_is_valid(self, session, tenant_a, build):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()

        session.add(
            build(
                tenant_a,
                payment_type=PaymentType.CREDIT,
                customer_id=customer.id,
                amount_paid=Decimal("20.00"),
            )
        )
        session.commit()

    @pytest.mark.parametrize("build", [cash_sale, quick_sale], ids=["detailed", "quick"])
    def test_money_received_needs_a_payment_method(self, session, tenant_a, build):
        assert_rejected(session, build(tenant_a, payment_method=None), match="payment_needs_method")

    def test_a_quick_sale_cannot_be_zero_but_a_free_detailed_bill_can(self, session, tenant_a):
        zero = {"total_amount": Decimal("0"), "amount_paid": Decimal("0"), "payment_method": None}
        assert_rejected(session, quick_sale(tenant_a, **zero), match="total_amount_positive")
        session.add(cash_sale(tenant_a, **zero))
        session.commit()

    def test_invoice_numbers_are_unique_per_shop_only(self, session, tenant_a, tenant_b):
        session.add_all(
            [
                cash_sale(tenant_a, invoice_no="A/1"),
                cash_sale(tenant_b, invoice_no="A/1", created_by=tenant_b.user.id),
            ]
        )
        session.commit()

        assert_rejected(session, cash_sale(tenant_a, invoice_no="A/1"), match="UNIQUE")

    def test_a_voided_document_needs_a_reason(self, session, tenant_a):
        assert_rejected(session, cash_sale(tenant_a, status=DocumentStatus.VOID), match="void_needs_reason")
        session.add(cash_sale(tenant_a, status=DocumentStatus.VOID, void_reason="Entered twice"))
        session.commit()


class TestSaleLineCost:
    def line(self, tenant: Tenant, sale: Sale, product: Product, **overrides) -> SaleItem:
        fields = {
            "shop_id": tenant.shop.id, "sale_id": sale.id, "product_id": product.id,
            "quantity": Decimal("2"), "unit_price": Decimal("50"), "line_total": Decimal("100"),
        }  # fmt: skip
        fields.update(overrides)
        return SaleItem(**fields)

    def setup_sale(self, session, tenant):
        sale = cash_sale(tenant)
        session.add(sale)
        product = make_product(session, tenant.shop, tenant.category)
        session.commit()
        return sale, product

    def test_unknown_cost_is_stored_as_null_never_zero(self, session, tenant_a):
        sale, product = self.setup_sale(session, tenant_a)
        item = self.line(tenant_a, sale, product)  # no cost
        session.add(item)
        session.commit()
        session.expire_all()

        assert item.unit_cost is None and item.cogs_amount is None

    def test_known_cost_needs_both_unit_cost_and_cogs(self, session, tenant_a):
        sale, product = self.setup_sale(session, tenant_a)

        assert_rejected(
            session,
            self.line(tenant_a, sale, product, unit_cost=Decimal("30")),
            match="cost_known_or_unknown",
        )
        session.add(self.line(tenant_a, sale, product, unit_cost=Decimal("30"), cogs_amount=Decimal("60")))
        session.commit()

    def test_quantity_must_be_positive(self, session, tenant_a):
        sale, product = self.setup_sale(session, tenant_a)

        assert_rejected(
            session, self.line(tenant_a, sale, product, quantity=Decimal("0")), match="quantity_positive"
        )

    def test_a_discount_larger_than_the_line_cannot_make_the_total_negative(self, session, tenant_a):
        sale, product = self.setup_sale(session, tenant_a)

        assert_rejected(
            session,
            self.line(tenant_a, sale, product, line_total=Decimal("-5")),
            match="line_total_non_negative",
        )


class TestPurchaseRules:
    def purchase(self, tenant: Tenant, supplier: Supplier, **overrides) -> Purchase:
        fields = {
            "shop_id": tenant.shop.id, "supplier_id": supplier.id, "supplier_invoice_no": "INV-9",
            "purchase_date": TODAY, "total_amount": Decimal("1000"), "amount_paid": Decimal("0"),
            "created_by": tenant.user.id,
        }  # fmt: skip
        fields.update(overrides)
        return Purchase(**fields)

    def test_supplier_invoice_number_cannot_repeat_for_the_same_supplier(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.add(self.purchase(tenant_a, supplier))
        session.commit()

        assert_rejected(session, self.purchase(tenant_a, supplier), match="UNIQUE")

    def test_different_suppliers_may_share_an_invoice_number(self, session, tenant_a):
        first = factories.make_supplier(session, tenant_a.shop, "First")
        second = factories.make_supplier(session, tenant_a.shop, "Second")
        session.add_all([self.purchase(tenant_a, first), self.purchase(tenant_a, second)])
        session.commit()

    def test_the_invoice_number_is_optional(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.add_all([self.purchase(tenant_a, supplier, supplier_invoice_no=None) for _ in range(2)])
        session.commit()

    def test_cannot_pay_more_than_the_total(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)

        assert_rejected(
            session,
            self.purchase(tenant_a, supplier, amount_paid=Decimal("2000"), payment_method=PaymentMethod.CASH),
            match="paid_not_above_total",
        )


class TestSystemTables:
    def test_document_sequence_is_unique_per_shop_type_and_year(self, session, tenant_a, tenant_b):
        sequence = dict(doc_type="SALE", fiscal_year="2026-27", last_number=1)
        session.add_all(
            [
                DocumentSequence(shop_id=tenant_a.shop.id, **sequence),
                DocumentSequence(shop_id=tenant_b.shop.id, **sequence),
            ]
        )
        session.commit()

        assert_rejected(session, DocumentSequence(shop_id=tenant_a.shop.id, **sequence), match="UNIQUE")
        assert_rejected(
            session,
            DocumentSequence(shop_id=tenant_a.shop.id, doc_type="X", fiscal_year="2026-27", last_number=-1),
            match="last_number_non_negative",
        )

    def test_idempotency_key_is_unique_per_shop(self, session, tenant_a, tenant_b):
        session.add_all(
            [
                IdempotencyKey(shop_id=t.shop.id, key="k1", operation="sale.create", request_hash="h")
                for t in (tenant_a, tenant_b)
            ]
        )
        session.commit()

        assert_rejected(
            session,
            IdempotencyKey(shop_id=tenant_a.shop.id, key="k1", operation="sale.create", request_hash="h"),
            match="UNIQUE",
        )

    def test_audit_log_stores_json_snapshots_and_cannot_point_at_another_shops_user(
        self, session, tenant_a, tenant_b
    ):
        entry = AuditLog(
            shop_id=tenant_a.shop.id,
            user_id=tenant_a.user.id,
            entity_type="product",
            entity_id=1,
            action="update",
            before_json={"price": "10.00"},
            after_json={"price": "12.00"},
        )
        session.add(entry)
        session.commit()
        session.expire_all()
        assert entry.after_json == {"price": "12.00"}

        assert_rejected(
            session,
            AuditLog(
                shop_id=tenant_a.shop.id, user_id=tenant_b.user.id, entity_type="product", action="update"
            ),
            match="FOREIGN KEY",
        )
