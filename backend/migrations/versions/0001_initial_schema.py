"""initial schema

Creates every Phase 2 table, seeds the shared units, and installs the insert-only protection on the
ledgers and the audit log.

This file is self-contained on purpose: it uses plain SQLAlchemy types and never imports from `app`,
so it keeps working unchanged after the models evolve. Money and quantity columns are BIGINT (paise and
thousandths, see app/db/types.py).

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, allows_decimal). Quantities in a unit with allows_decimal = false must be whole numbers;
# services enforce this because it needs a lookup in another table.
UNITS = [
    ("pcs", "Piece", False),
    ("kg", "Kilogram", True),
    ("g", "Gram", False),
    ("L", "Litre", True),
    ("ml", "Millilitre", False),
    ("pkt", "Packet", False),
    ("box", "Box", False),
    ("doz", "Dozen", True),
]

# Tables whose rows may only ever be inserted. Corrections are new rows, never updates or deletes.
INSERT_ONLY_TABLES = ["inventory_transactions", "customer_ledger", "audit_log"]


def _create_insert_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in INSERT_ONLY_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is insert-only'); END"
                )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION kirana_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION '% is insert-only', TG_TABLE_NAME; END; $$"
        )
        for table in INSERT_ONLY_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION kirana_forbid_change()"
            )
    else:
        raise NotImplementedError(f"Insert-only triggers are not defined for {dialect}")


def _drop_insert_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in INSERT_ONLY_TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
    elif dialect == "postgresql":
        for table in INSERT_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_insert_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS kirana_forbid_change()")


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("upi_id", sa.String(length=100), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="Asia/Kolkata", nullable=False),
        sa.Column(
            "language",
            sa.Enum("en", "hi", name="language", native_enum=False, create_constraint=True),
            server_default="en",
            nullable=False,
        ),
        sa.Column("allow_negative_stock", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "mrp_validation_mode",
            sa.Enum("WARN", "BLOCK", name="mrp_validation_mode", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(address)) > 0", name=op.f("ck_shops_address_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_shops_name_not_blank")),
        sa.CheckConstraint("length(trim(phone)) > 0", name=op.f("ck_shops_phone_not_blank")),
        sa.CheckConstraint("length(trim(timezone)) > 0", name=op.f("ck_shops_timezone_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shops")),
    )
    op.create_table(
        "units",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("allows_decimal", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_units_code_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_units_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_units")),
        sa.UniqueConstraint("code", name=op.f("uq_units_code")),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_categories_name_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_categories_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_categories_shop_id_id")),
        sa.UniqueConstraint("shop_id", "name", name=op.f("uq_categories_shop_id_name")),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_customers_name_not_blank")),
        sa.CheckConstraint("length(trim(phone)) > 0", name=op.f("ck_customers_phone_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_customers_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_customers_shop_id_id")),
        sa.UniqueConstraint("shop_id", "phone", name=op.f("uq_customers_shop_id_phone")),
    )
    op.create_table(
        "document_sequences",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=False),
        sa.Column("fiscal_year", sa.String(length=9), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_number >= 0", name=op.f("ck_document_sequences_last_number_non_negative")),
        sa.CheckConstraint(
            "length(trim(doc_type)) > 0", name=op.f("ck_document_sequences_doc_type_not_blank")
        ),
        sa.CheckConstraint(
            "length(trim(fiscal_year)) > 0", name=op.f("ck_document_sequences_fiscal_year_not_blank")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_document_sequences_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_sequences")),
        sa.UniqueConstraint(
            "shop_id",
            "doc_type",
            "fiscal_year",
            name=op.f("uq_document_sequences_shop_id_doc_type_fiscal_year"),
        ),
    )
    op.create_table(
        "expense_categories",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_expense_categories_name_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_expense_categories_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_categories")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_expense_categories_shop_id_id")),
        sa.UniqueConstraint("shop_id", "name", name=op.f("uq_expense_categories_shop_id_name")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(key)) > 0", name=op.f("ck_idempotency_keys_key_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_idempotency_keys_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("shop_id", "key", name=op.f("uq_idempotency_keys_shop_id_key")),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_suppliers_name_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_suppliers_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_suppliers_shop_id_id")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "STAFF", name="role", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint("length(trim(email)) > 0", name=op.f("ck_users_email_not_blank")),
        sa.CheckConstraint("length(trim(full_name)) > 0", name=op.f("ck_users_full_name_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_users_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_users_shop_id_id")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(action)) > 0", name=op.f("ck_audit_log_action_not_blank")),
        sa.CheckConstraint("length(trim(entity_type)) > 0", name=op.f("ck_audit_log_entity_type_not_blank")),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"], ["users.shop_id", "users.id"], name=op.f("fk_audit_log_shop_id_user_id")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_audit_log_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_log_shop_created", ["shop_id", "created_at"], unique=False)
        batch_op.create_index(
            "ix_audit_log_shop_entity", ["shop_id", "entity_type", "entity_id"], unique=False
        )

    op.create_table(
        "customer_ledger",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum(
                "OPENING_BALANCE",
                "CREDIT_SALE",
                "PAYMENT",
                "RETURN_CREDIT",
                "ADJUSTMENT",
                "REVERSAL",
                name="entry_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount_delta", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum("CASH", "UPI", "OTHER", name="payment_method", native_enum=False, create_constraint=True),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(length=100), nullable=True),
        sa.Column(
            "reference_type",
            sa.Enum(
                "SALE",
                "QUICK_SALE",
                "SALES_RETURN",
                name="reference_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("reference_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("reverses_entry_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(entry_type = 'CREDIT_SALE' AND amount_delta > 0) OR (entry_type IN ('PAYMENT', 'RETURN_CREDIT') AND amount_delta < 0) OR (entry_type IN ('OPENING_BALANCE', 'ADJUSTMENT', 'REVERSAL') AND amount_delta <> 0)",
            name=op.f("ck_customer_ledger_sign_matches_type"),
        ),
        sa.CheckConstraint(
            "(entry_type = 'REVERSAL' AND reverses_entry_id IS NOT NULL) OR (entry_type <> 'REVERSAL' AND reverses_entry_id IS NULL)",
            name=op.f("ck_customer_ledger_reversal_links_original"),
        ),
        sa.CheckConstraint(
            "payment_method IS NULL OR entry_type = 'PAYMENT'",
            name=op.f("ck_customer_ledger_method_only_for_payment"),
        ),
        sa.CheckConstraint(
            "(reference_type IS NULL AND reference_id IS NULL) OR (reference_type IS NOT NULL AND reference_id IS NOT NULL)",
            name=op.f("ck_customer_ledger_reference_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_customer_ledger_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_customer_ledger_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "reverses_entry_id"],
            ["customer_ledger.shop_id", "customer_ledger.id"],
            name=op.f("fk_customer_ledger_shop_id_reverses_entry_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_customer_ledger_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_ledger")),
        sa.UniqueConstraint("reverses_entry_id", name="uq_customer_ledger_reverses"),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_customer_ledger_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "reference_type", "reference_id", "entry_type", name="uq_customer_ledger_reference"
        ),
    )
    with op.batch_alter_table("customer_ledger", schema=None) as batch_op:
        batch_op.create_index(
            "ix_customer_ledger_shop_customer_date", ["shop_id", "customer_id", "entry_date"], unique=False
        )

    op.create_table(
        "expenses",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum("CASH", "UPI", "OTHER", name="payment_method", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_expenses_void_needs_reason")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_expenses_amount_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "category_id"],
            ["expense_categories.shop_id", "expense_categories.id"],
            name=op.f("fk_expenses_shop_id_category_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_expenses_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["expenses.shop_id", "expenses.id"],
            name=op.f("fk_expenses_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_expenses_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenses")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_expenses_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_expenses_shop_id_id")),
    )
    with op.batch_alter_table("expenses", schema=None) as batch_op:
        batch_op.create_index("ix_expenses_shop_date", ["shop_id", "expense_date"], unique=False)

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sku", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("category_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("unit_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("default_supplier_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("reorder_level", sa.BigInteger(), nullable=False),
        sa.Column("mrp", sa.BigInteger(), nullable=True),
        sa.Column("selling_price", sa.BigInteger(), nullable=False),
        sa.Column("purchase_price", sa.BigInteger(), nullable=True),
        sa.Column("avg_cost", sa.BigInteger(), nullable=True),
        sa.Column("barcode", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("avg_cost >= 0", name=op.f("ck_products_avg_cost_non_negative")),
        sa.CheckConstraint("length(trim(barcode)) > 0", name=op.f("ck_products_barcode_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_products_name_not_blank")),
        sa.CheckConstraint("length(trim(sku)) > 0", name=op.f("ck_products_sku_not_blank")),
        sa.CheckConstraint("mrp >= 0", name=op.f("ck_products_mrp_non_negative")),
        sa.CheckConstraint("purchase_price >= 0", name=op.f("ck_products_purchase_price_non_negative")),
        sa.CheckConstraint("reorder_level >= 0", name=op.f("ck_products_reorder_level_non_negative")),
        sa.CheckConstraint("selling_price >= 0", name=op.f("ck_products_selling_price_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "category_id"],
            ["categories.shop_id", "categories.id"],
            name=op.f("fk_products_shop_id_category_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "default_supplier_id"],
            ["suppliers.shop_id", "suppliers.id"],
            name=op.f("fk_products_shop_id_default_supplier_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_products_shop_id")),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name=op.f("fk_products_unit_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("shop_id", "barcode", name=op.f("uq_products_shop_id_barcode")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_products_shop_id_id")),
        sa.UniqueConstraint("shop_id", "sku", name=op.f("uq_products_shop_id_sku")),
    )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_index("ix_products_shop_id_category_id", ["shop_id", "category_id"], unique=False)
        batch_op.create_index("ix_products_shop_id_name", ["shop_id", "name"], unique=False)

    op.create_table(
        "purchases",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("supplier_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("supplier_invoice_no", sa.String(length=50), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("amount_paid", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum("CASH", "UPI", "OTHER", name="payment_method", native_enum=False, create_constraint=True),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_purchases_void_needs_reason")
        ),
        sa.CheckConstraint("amount_paid <= total_amount", name=op.f("ck_purchases_paid_not_above_total")),
        sa.CheckConstraint(
            "amount_paid = 0 OR payment_method IS NOT NULL", name=op.f("ck_purchases_payment_needs_method")
        ),
        sa.CheckConstraint("amount_paid >= 0", name=op.f("ck_purchases_amount_paid_non_negative")),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_purchases_total_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_purchases_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["purchases.shop_id", "purchases.id"],
            name=op.f("fk_purchases_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "supplier_id"],
            ["suppliers.shop_id", "suppliers.id"],
            name=op.f("fk_purchases_shop_id_supplier_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_purchases_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchases")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_purchases_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_purchases_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id",
            "supplier_id",
            "supplier_invoice_no",
            name=op.f("uq_purchases_shop_id_supplier_id_supplier_invoice_no"),
        ),
    )
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.create_index("ix_purchases_shop_date", ["shop_id", "purchase_date"], unique=False)

    op.create_table(
        "quick_sales",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "payment_type",
            sa.Enum("PAID", "CREDIT", name="payment_type", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("amount_paid", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum("CASH", "UPI", "OTHER", name="payment_method", native_enum=False, create_constraint=True),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(length=100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payment_type <> 'CREDIT' OR (customer_id IS NOT NULL AND amount_paid < total_amount)",
            name=op.f("ck_quick_sales_credit_needs_customer_and_balance"),
        ),
        sa.CheckConstraint(
            "payment_type <> 'PAID' OR amount_paid = total_amount",
            name=op.f("ck_quick_sales_paid_means_fully_paid"),
        ),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_quick_sales_void_needs_reason")
        ),
        sa.CheckConstraint(
            "amount_paid = 0 OR payment_method IS NOT NULL", name=op.f("ck_quick_sales_payment_needs_method")
        ),
        sa.CheckConstraint("amount_paid >= 0", name=op.f("ck_quick_sales_amount_paid_non_negative")),
        sa.CheckConstraint("total_amount > 0", name=op.f("ck_quick_sales_total_amount_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_quick_sales_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_quick_sales_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["quick_sales.shop_id", "quick_sales.id"],
            name=op.f("fk_quick_sales_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_quick_sales_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quick_sales")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_quick_sales_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_quick_sales_shop_id_id")),
    )
    with op.batch_alter_table("quick_sales", schema=None) as batch_op:
        batch_op.create_index("ix_quick_sales_shop_date", ["shop_id", "sale_date"], unique=False)

    op.create_table(
        "sales",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("invoice_no", sa.String(length=30), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_type",
            sa.Enum("PAID", "CREDIT", name="payment_type", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("amount_paid", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum("CASH", "UPI", "OTHER", name="payment_method", native_enum=False, create_constraint=True),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payment_type <> 'CREDIT' OR (customer_id IS NOT NULL AND amount_paid < total_amount)",
            name=op.f("ck_sales_credit_needs_customer_and_balance"),
        ),
        sa.CheckConstraint(
            "payment_type <> 'PAID' OR amount_paid = total_amount",
            name=op.f("ck_sales_paid_means_fully_paid"),
        ),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_sales_void_needs_reason")
        ),
        sa.CheckConstraint(
            "amount_paid = 0 OR payment_method IS NOT NULL", name=op.f("ck_sales_payment_needs_method")
        ),
        sa.CheckConstraint("amount_paid >= 0", name=op.f("ck_sales_amount_paid_non_negative")),
        sa.CheckConstraint("length(trim(invoice_no)) > 0", name=op.f("ck_sales_invoice_no_not_blank")),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_sales_total_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_sales_shop_id_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_sales_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["sales.shop_id", "sales.id"],
            name=op.f("fk_sales_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sales_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_sales_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sales_shop_id_id")),
        sa.UniqueConstraint("shop_id", "invoice_no", name=op.f("uq_sales_shop_id_invoice_no")),
    )
    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.create_index("ix_sales_shop_customer", ["shop_id", "customer_id"], unique=False)
        batch_op.create_index("ix_sales_shop_date", ["shop_id", "sale_date"], unique=False)

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "txn_type",
            sa.Enum(
                "OPENING",
                "PURCHASE",
                "SALE",
                "SALE_RETURN",
                "PURCHASE_RETURN",
                "ADJUSTMENT",
                "REVERSAL",
                name="txn_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("qty_delta", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost", sa.BigInteger(), nullable=True),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column(
            "reference_type",
            sa.Enum(
                "PRODUCT",
                "PURCHASE_ITEM",
                "SALE_ITEM",
                "PURCHASE_RETURN_ITEM",
                "SALES_RETURN_ITEM",
                name="reference_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("reference_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("reverses_txn_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "reason_code",
            sa.Enum(
                "CUSTOMER_RETURN_NO_BILL",
                "COUNT_CORRECTION",
                "DAMAGED",
                "EXPIRED",
                "LOST",
                "OTHER",
                name="reason_code",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(txn_type = 'ADJUSTMENT' AND reason_code IS NOT NULL) OR (txn_type <> 'ADJUSTMENT' AND reason_code IS NULL)",
            name=op.f("ck_inventory_transactions_reason_only_for_adjustment"),
        ),
        sa.CheckConstraint(
            "(txn_type = 'REVERSAL' AND reverses_txn_id IS NOT NULL) OR (txn_type <> 'REVERSAL' AND reverses_txn_id IS NULL)",
            name=op.f("ck_inventory_transactions_reversal_links_original"),
        ),
        sa.CheckConstraint(
            "(txn_type IN ('OPENING', 'PURCHASE', 'SALE_RETURN') AND qty_delta > 0) OR (txn_type IN ('SALE', 'PURCHASE_RETURN') AND qty_delta < 0) OR (txn_type IN ('ADJUSTMENT', 'REVERSAL') AND qty_delta <> 0)",
            name=op.f("ck_inventory_transactions_sign_matches_type"),
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code <> 'OTHER' OR length(trim(coalesce(note, ''))) > 0",
            name=op.f("ck_inventory_transactions_other_reason_needs_note"),
        ),
        sa.CheckConstraint(
            "(reference_type IS NULL AND reference_id IS NULL) OR (reference_type IS NOT NULL AND reference_id IS NOT NULL)",
            name=op.f("ck_inventory_transactions_reference_pair"),
        ),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_inventory_transactions_unit_cost_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_inventory_transactions_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_inventory_transactions_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "reverses_txn_id"],
            ["inventory_transactions.shop_id", "inventory_transactions.id"],
            name=op.f("fk_inventory_transactions_shop_id_reverses_txn_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_inventory_transactions_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_transactions")),
        sa.UniqueConstraint("reverses_txn_id", name="uq_inventory_transactions_reverses"),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_inventory_transactions_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id",
            "reference_type",
            "reference_id",
            "txn_type",
            name="uq_inventory_transactions_reference",
        ),
    )
    with op.batch_alter_table("inventory_transactions", schema=None) as batch_op:
        batch_op.create_index("ix_inventory_transactions_shop_date", ["shop_id", "txn_date"], unique=False)
        batch_op.create_index(
            "ix_inventory_transactions_shop_product_date", ["shop_id", "product_id", "txn_date"], unique=False
        )

    op.create_table(
        "purchase_items",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("purchase_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost", sa.BigInteger(), nullable=False),
        sa.Column("line_total", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("line_total >= 0", name=op.f("ck_purchase_items_line_total_non_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_purchase_items_quantity_positive")),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_purchase_items_unit_cost_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_purchase_items_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "purchase_id"],
            ["purchases.shop_id", "purchases.id"],
            name=op.f("fk_purchase_items_shop_id_purchase_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_purchase_items_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_items")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_purchase_items_shop_id_id")),
    )
    with op.batch_alter_table("purchase_items", schema=None) as batch_op:
        batch_op.create_index("ix_purchase_items_shop_product", ["shop_id", "product_id"], unique=False)
        batch_op.create_index("ix_purchase_items_shop_purchase", ["shop_id", "purchase_id"], unique=False)

    op.create_table(
        "purchase_returns",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("purchase_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column(
            "credit_mode",
            sa.Enum(
                "CASH",
                "UPI",
                "SUPPLIER_CREDIT",
                name="credit_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_purchase_returns_void_needs_reason")
        ),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_purchase_returns_total_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_purchase_returns_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "purchase_id"],
            ["purchases.shop_id", "purchases.id"],
            name=op.f("fk_purchase_returns_shop_id_purchase_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["purchase_returns.shop_id", "purchase_returns.id"],
            name=op.f("fk_purchase_returns_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_purchase_returns_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_returns")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_purchase_returns_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_purchase_returns_shop_id_id")),
    )
    with op.batch_alter_table("purchase_returns", schema=None) as batch_op:
        batch_op.create_index("ix_purchase_returns_shop_date", ["shop_id", "return_date"], unique=False)

    op.create_table(
        "sale_items",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sale_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("mrp", sa.BigInteger(), nullable=True),
        sa.Column("discount", sa.BigInteger(), nullable=False),
        sa.Column("line_total", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost", sa.BigInteger(), nullable=True),
        sa.Column("cogs_amount", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(unit_cost IS NULL AND cogs_amount IS NULL) OR (unit_cost IS NOT NULL AND cogs_amount IS NOT NULL)",
            name=op.f("ck_sale_items_cost_known_or_unknown"),
        ),
        sa.CheckConstraint("cogs_amount >= 0", name=op.f("ck_sale_items_cogs_amount_non_negative")),
        sa.CheckConstraint("discount >= 0", name=op.f("ck_sale_items_discount_non_negative")),
        sa.CheckConstraint("line_total >= 0", name=op.f("ck_sale_items_line_total_non_negative")),
        sa.CheckConstraint("mrp >= 0", name=op.f("ck_sale_items_mrp_non_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_sale_items_quantity_positive")),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_sale_items_unit_cost_non_negative")),
        sa.CheckConstraint("unit_price >= 0", name=op.f("ck_sale_items_unit_price_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_sale_items_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sale_id"], ["sales.shop_id", "sales.id"], name=op.f("fk_sale_items_shop_id_sale_id")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sale_items_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_items")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sale_items_shop_id_id")),
    )
    with op.batch_alter_table("sale_items", schema=None) as batch_op:
        batch_op.create_index("ix_sale_items_shop_product", ["shop_id", "product_id"], unique=False)
        batch_op.create_index("ix_sale_items_shop_sale", ["shop_id", "sale_id"], unique=False)

    op.create_table(
        "sales_returns",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sale_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column(
            "refund_mode",
            sa.Enum("CASH", "UPI", "KHATA", name="refund_mode", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("total_refund", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("POSTED", "VOID", name="status", native_enum=False, create_constraint=True),
            server_default="POSTED",
            nullable=False,
        ),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'POSTED' OR void_reason IS NOT NULL", name=op.f("ck_sales_returns_void_needs_reason")
        ),
        sa.CheckConstraint("total_refund >= 0", name=op.f("ck_sales_returns_total_refund_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_sales_returns_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["sales_returns.shop_id", "sales_returns.id"],
            name=op.f("fk_sales_returns_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sale_id"],
            ["sales.shop_id", "sales.id"],
            name=op.f("fk_sales_returns_shop_id_sale_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sales_returns_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_returns")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_sales_returns_replaces_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sales_returns_shop_id_id")),
    )
    with op.batch_alter_table("sales_returns", schema=None) as batch_op:
        batch_op.create_index("ix_sales_returns_shop_date", ["shop_id", "return_date"], unique=False)

    op.create_table(
        "purchase_return_items",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("purchase_return_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("purchase_item_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost", sa.BigInteger(), nullable=False),
        sa.Column("line_total", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("line_total >= 0", name=op.f("ck_purchase_return_items_line_total_non_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_purchase_return_items_quantity_positive")),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_purchase_return_items_unit_cost_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_purchase_return_items_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "purchase_item_id"],
            ["purchase_items.shop_id", "purchase_items.id"],
            name=op.f("fk_purchase_return_items_shop_id_purchase_item_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "purchase_return_id"],
            ["purchase_returns.shop_id", "purchase_returns.id"],
            name=op.f("fk_purchase_return_items_shop_id_purchase_return_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_purchase_return_items_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_return_items")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_purchase_return_items_shop_id_id")),
    )
    with op.batch_alter_table("purchase_return_items", schema=None) as batch_op:
        batch_op.create_index(
            "ix_purchase_return_items_shop_item", ["shop_id", "purchase_item_id"], unique=False
        )
        batch_op.create_index(
            "ix_purchase_return_items_shop_return", ["shop_id", "purchase_return_id"], unique=False
        )

    op.create_table(
        "sales_return_items",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sales_return_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sale_item_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("refund_amount", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost", sa.BigInteger(), nullable=True),
        sa.Column("cogs_amount", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(unit_cost IS NULL AND cogs_amount IS NULL) OR (unit_cost IS NOT NULL AND cogs_amount IS NOT NULL)",
            name=op.f("ck_sales_return_items_cost_known_or_unknown"),
        ),
        sa.CheckConstraint("cogs_amount >= 0", name=op.f("ck_sales_return_items_cogs_amount_non_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_sales_return_items_quantity_positive")),
        sa.CheckConstraint(
            "refund_amount >= 0", name=op.f("ck_sales_return_items_refund_amount_non_negative")
        ),
        sa.CheckConstraint("unit_cost >= 0", name=op.f("ck_sales_return_items_unit_cost_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_sales_return_items_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sale_item_id"],
            ["sale_items.shop_id", "sale_items.id"],
            name=op.f("fk_sales_return_items_shop_id_sale_item_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sales_return_id"],
            ["sales_returns.shop_id", "sales_returns.id"],
            name=op.f("fk_sales_return_items_shop_id_sales_return_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sales_return_items_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_return_items")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sales_return_items_shop_id_id")),
    )
    with op.batch_alter_table("sales_return_items", schema=None) as batch_op:
        batch_op.create_index("ix_sales_return_items_shop_item", ["shop_id", "sale_item_id"], unique=False)
        batch_op.create_index(
            "ix_sales_return_items_shop_return", ["shop_id", "sales_return_id"], unique=False
        )

    # --- Shared reference data: units of measure ---------------------------------------------------
    units = sa.table(
        "units",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("allows_decimal", sa.Boolean),
    )
    op.bulk_insert(
        units,
        [
            {"code": code, "name": name, "allows_decimal": allows_decimal}
            for code, name, allows_decimal in UNITS
        ],
    )

    # --- Insert-only protection for the ledgers and the audit log ----------------------------------
    _create_insert_only_triggers()


def downgrade() -> None:
    _drop_insert_only_triggers()
    with op.batch_alter_table("sales_return_items", schema=None) as batch_op:
        batch_op.drop_index("ix_sales_return_items_shop_return")
        batch_op.drop_index("ix_sales_return_items_shop_item")

    op.drop_table("sales_return_items")
    with op.batch_alter_table("purchase_return_items", schema=None) as batch_op:
        batch_op.drop_index("ix_purchase_return_items_shop_return")
        batch_op.drop_index("ix_purchase_return_items_shop_item")

    op.drop_table("purchase_return_items")
    with op.batch_alter_table("sales_returns", schema=None) as batch_op:
        batch_op.drop_index("ix_sales_returns_shop_date")

    op.drop_table("sales_returns")
    with op.batch_alter_table("sale_items", schema=None) as batch_op:
        batch_op.drop_index("ix_sale_items_shop_sale")
        batch_op.drop_index("ix_sale_items_shop_product")

    op.drop_table("sale_items")
    with op.batch_alter_table("purchase_returns", schema=None) as batch_op:
        batch_op.drop_index("ix_purchase_returns_shop_date")

    op.drop_table("purchase_returns")
    with op.batch_alter_table("purchase_items", schema=None) as batch_op:
        batch_op.drop_index("ix_purchase_items_shop_purchase")
        batch_op.drop_index("ix_purchase_items_shop_product")

    op.drop_table("purchase_items")
    with op.batch_alter_table("inventory_transactions", schema=None) as batch_op:
        batch_op.drop_index("ix_inventory_transactions_shop_product_date")
        batch_op.drop_index("ix_inventory_transactions_shop_date")

    op.drop_table("inventory_transactions")
    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.drop_index("ix_sales_shop_date")
        batch_op.drop_index("ix_sales_shop_customer")

    op.drop_table("sales")
    with op.batch_alter_table("quick_sales", schema=None) as batch_op:
        batch_op.drop_index("ix_quick_sales_shop_date")

    op.drop_table("quick_sales")
    with op.batch_alter_table("purchases", schema=None) as batch_op:
        batch_op.drop_index("ix_purchases_shop_date")

    op.drop_table("purchases")
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index("ix_products_shop_id_name")
        batch_op.drop_index("ix_products_shop_id_category_id")

    op.drop_table("products")
    with op.batch_alter_table("expenses", schema=None) as batch_op:
        batch_op.drop_index("ix_expenses_shop_date")

    op.drop_table("expenses")
    with op.batch_alter_table("customer_ledger", schema=None) as batch_op:
        batch_op.drop_index("ix_customer_ledger_shop_customer_date")

    op.drop_table("customer_ledger")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_shop_entity")
        batch_op.drop_index("ix_audit_log_shop_created")

    op.drop_table("audit_log")
    op.drop_table("users")
    op.drop_table("suppliers")
    op.drop_table("idempotency_keys")
    op.drop_table("expense_categories")
    op.drop_table("document_sequences")
    op.drop_table("customers")
    op.drop_table("categories")
    op.drop_table("units")
    op.drop_table("shops")
