"""Finance, accounting and business control (Phase 15)

* New: insert-only `finance_entries` (money events finance itself records, and their reversals), insert-only
  `cash_counts`, insert-only `reconciliation_marks`, `financial_periods`, `tax_settings`, `tax_rates`,
  `finance_settings`. Sales, purchases, returns and khata are NOT copied: finance reads them where they are.
* `expenses` is rebuilt: the Phase 1 table was a foundation nothing ever wrote to, and its status and payment-method
  columns cannot express the new lifecycle, so it is replaced (the migration refuses to run if it holds rows).
* `expense_categories` gains an optional `cash_flow_class`.
* Seeds the finance permissions into the system roles.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INSERT_ONLY = ["finance_entries", "cash_counts", "reconciliation_marks"]

NEW_PERMISSIONS = {
    "OWNER": [
        "FINANCE_VIEW", "FINANCE_MANAGE", "FINANCE_EXPENSE_VIEW", "FINANCE_EXPENSE_MANAGE", "FINANCE_EXPENSE_APPROVE",
        "FINANCE_CASH_VIEW", "FINANCE_CASH_MANAGE", "FINANCE_RECONCILIATION_MANAGE", "FINANCE_PERIOD_LOCK",
        "FINANCE_PERIOD_UNLOCK", "FINANCE_ADJUSTMENT_MANAGE", "FINANCE_EXPORT",
    ],
    "MANAGER": [
        "FINANCE_VIEW", "FINANCE_MANAGE", "FINANCE_EXPENSE_VIEW", "FINANCE_EXPENSE_MANAGE", "FINANCE_EXPENSE_APPROVE",
        "FINANCE_CASH_VIEW", "FINANCE_CASH_MANAGE", "FINANCE_RECONCILIATION_MANAGE", "FINANCE_PERIOD_LOCK",
        "FINANCE_PERIOD_UNLOCK", "FINANCE_ADJUSTMENT_MANAGE", "FINANCE_EXPORT",
    ],
    "ACCOUNTANT": [
        "FINANCE_VIEW", "FINANCE_EXPENSE_VIEW", "FINANCE_EXPENSE_MANAGE", "FINANCE_CASH_VIEW", "FINANCE_CASH_MANAGE",
        "FINANCE_RECONCILIATION_MANAGE", "FINANCE_PERIOD_LOCK", "FINANCE_EXPORT",
    ],
    "CASHIER": ["FINANCE_CASH_VIEW", "FINANCE_CASH_MANAGE"],
}  # fmt: skip


def _create_triggers() -> None:
    dialect = op.get_bind().dialect.name
    for table in INSERT_ONLY:
        if dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is insert-only'); END"
                )
        elif dialect == "postgresql":
            op.execute(
                f"CREATE TRIGGER trg_{table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION kirana_forbid_change()"
            )


def _drop_triggers() -> None:
    dialect = op.get_bind().dialect.name
    for table in INSERT_ONLY:
        if dialect == "sqlite":
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
        elif dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_insert_only ON {table}")


def upgrade() -> None:
    if (
        not context.is_offline_mode()
        and op.get_bind().execute(sa.text("SELECT COUNT(*) FROM expenses")).scalar()
    ):
        raise RuntimeError(
            "expenses already holds rows; Phase 15 replaces that table and will not discard data"
        )
    op.drop_table("expenses")
    op.create_table(
        "finance_settings",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("expense_approval_threshold", sa.BigInteger(), nullable=True),
        sa.Column("adjustment_approval_threshold", sa.BigInteger(), nullable=True),
        sa.Column("cash_adjustment_threshold", sa.BigInteger(), nullable=True),
        sa.Column("period_reopen_requires_approval", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("overdue_after_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("expense_spike_pct", sa.Integer(), server_default="50", nullable=False),
        sa.Column("margin_drop_points", sa.Integer(), server_default="5", nullable=False),
        sa.Column("cash_variance_alert_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "adjustment_approval_threshold >= 0",
            name=op.f("ck_finance_settings_adjustment_approval_threshold_non_negative"),
        ),
        sa.CheckConstraint(
            "cash_adjustment_threshold >= 0",
            name=op.f("ck_finance_settings_cash_adjustment_threshold_non_negative"),
        ),
        sa.CheckConstraint(
            "cash_variance_alert_amount >= 0",
            name=op.f("ck_finance_settings_cash_variance_alert_amount_non_negative"),
        ),
        sa.CheckConstraint(
            "expense_approval_threshold >= 0",
            name=op.f("ck_finance_settings_expense_approval_threshold_non_negative"),
        ),
        sa.CheckConstraint(
            "expense_spike_pct >= 0", name=op.f("ck_finance_settings_expense_spike_pct_non_negative")
        ),
        sa.CheckConstraint(
            "margin_drop_points >= 0", name=op.f("ck_finance_settings_margin_drop_points_non_negative")
        ),
        sa.CheckConstraint(
            "overdue_after_days > 0", name=op.f("ck_finance_settings_overdue_after_days_positive")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_finance_settings_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_settings")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_finance_settings_shop_id_id")),
        sa.UniqueConstraint("shop_id", name=op.f("uq_finance_settings_shop_id")),
    )
    op.create_table(
        "tax_settings",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "tax_type",
            sa.Enum(
                "GST", "VAT", "SALES_TAX", "OTHER", name="tax_type", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("registration_number", sa.String(length=30), nullable=True),
        sa.Column("location_state", sa.String(length=60), nullable=True),
        sa.Column("prices_include_tax", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_tax_settings_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_settings")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_tax_settings_shop_id_id")),
        sa.UniqueConstraint("shop_id", name=op.f("uq_tax_settings_shop_id")),
    )
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("rate_bp", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("tax_category", sa.String(length=60), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_tax_rates_name_not_blank")),
        sa.CheckConstraint("rate_bp >= 0 AND rate_bp <= 10000", name=op.f("ck_tax_rates_rate_bp_in_range")),
        sa.ForeignKeyConstraint(
            ["shop_id", "category_id"],
            ["categories.shop_id", "categories.id"],
            name=op.f("fk_tax_rates_shop_id_category_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_tax_rates_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rates")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_tax_rates_shop_id_id")),
        sa.UniqueConstraint("shop_id", "name", name=op.f("uq_tax_rates_shop_id_name")),
    )
    op.create_table(
        "cash_counts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("count_date", sa.Date(), nullable=False),
        sa.Column("expected_cash", sa.BigInteger(), nullable=True),
        sa.Column("actual_cash", sa.BigInteger(), nullable=False),
        sa.Column("difference", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("counted_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("actual_cash >= 0", name=op.f("ck_cash_counts_actual_cash_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "counted_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_cash_counts_shop_id_counted_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_cash_counts_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_counts")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_cash_counts_shop_id_id")),
    )
    with op.batch_alter_table("cash_counts", schema=None) as batch_op:
        batch_op.create_index("ix_cash_counts_shop_date", ["shop_id", "count_date"], unique=False)

    op.create_table(
        "expenses",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("expense_no", sa.String(length=30), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("payee", sa.String(length=150), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum(
                "CASH",
                "UPI",
                "CARD",
                "BANK_TRANSFER",
                "OTHER",
                name="finance_payment_method",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attachment_ref", sa.String(length=300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "APPROVED",
                "POSTED",
                "REJECTED",
                "VOIDED",
                name="expense_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("requires_approval", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("posted_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaces_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name=op.f("ck_expenses_reject_needs_reason"),
        ),
        sa.CheckConstraint(
            "status <> 'VOIDED' OR void_reason IS NOT NULL", name=op.f("ck_expenses_void_needs_reason")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_expenses_amount_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "approved_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_expenses_shop_id_approved_by"),
        ),
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
            ["shop_id", "posted_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_expenses_shop_id_posted_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "replaces_id"],
            ["expenses.shop_id", "expenses.id"],
            name=op.f("fk_expenses_shop_id_replaces_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_expenses_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenses")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_expenses_replaces_id")),
        sa.UniqueConstraint("shop_id", "expense_no", name=op.f("uq_expenses_shop_id_expense_no")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_expenses_shop_id_id")),
    )
    with op.batch_alter_table("expenses", schema=None) as batch_op:
        batch_op.create_index("ix_expenses_shop_date", ["shop_id", "expense_date"], unique=False)
        batch_op.create_index("ix_expenses_shop_status", ["shop_id", "status"], unique=False)

    op.create_table(
        "finance_entries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "SALE",
                "PURCHASE",
                "SALE_RETURN",
                "PURCHASE_RETURN",
                "CUSTOMER_PAYMENT",
                "SUPPLIER_PAYMENT",
                "EXPENSE",
                "OWNER_CAPITAL",
                "OWNER_WITHDRAWAL",
                "ADJUSTMENT",
                "OTHER_INCOME",
                name="finance_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum("IN", "OUT", name="flow_direction", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum(
                "CASH",
                "UPI",
                "CARD",
                "BANK_TRANSFER",
                "OTHER",
                name="finance_payment_method",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("reference_type", sa.String(length=30), nullable=True),
        sa.Column("reference_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("supplier_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "cash_flow_class",
            sa.Enum(
                "OPERATING",
                "INVESTING",
                "FINANCING",
                "OTHER",
                name="cash_flow_class",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("reverses_entry_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_finance_entries_amount_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_finance_entries_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_finance_entries_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "reverses_entry_id"],
            ["finance_entries.shop_id", "finance_entries.id"],
            name=op.f("fk_finance_entries_shop_id_reverses_entry_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "supplier_id"],
            ["suppliers.shop_id", "suppliers.id"],
            name=op.f("fk_finance_entries_shop_id_supplier_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_finance_entries_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_entries")),
        sa.UniqueConstraint("reverses_entry_id", name="uq_finance_entries_reverses"),
        sa.UniqueConstraint(
            "shop_id", "event_type", "reference_type", "reference_id", name="uq_finance_entries_reference"
        ),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_finance_entries_shop_id_id")),
    )
    with op.batch_alter_table("finance_entries", schema=None) as batch_op:
        batch_op.create_index("ix_finance_entries_shop_date", ["shop_id", "entry_date"], unique=False)

    op.create_table(
        "financial_periods",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN", "LOCKED", "CLOSED", name="period_status", native_enum=False, create_constraint=True
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("status_changed_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period_end >= period_start", name=op.f("ck_financial_periods_period_dates_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_financial_periods_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "status_changed_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_financial_periods_shop_id_status_changed_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_financial_periods_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_periods")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_financial_periods_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "period_start", name=op.f("uq_financial_periods_shop_id_period_start")
        ),
    )
    with op.batch_alter_table("financial_periods", schema=None) as batch_op:
        batch_op.create_index(
            "ix_financial_periods_shop_range", ["shop_id", "period_start", "period_end"], unique=False
        )

    op.create_table(
        "reconciliation_marks",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "MATCHED",
                "UNMATCHED",
                "PARTIAL",
                "REVIEW_REQUIRED",
                name="recon_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("confirmed_amount", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(source_type)) > 0", name=op.f("ck_reconciliation_marks_source_type_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_reconciliation_marks_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_reconciliation_marks_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_marks")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_reconciliation_marks_shop_id_id")),
    )
    with op.batch_alter_table("reconciliation_marks", schema=None) as batch_op:
        batch_op.create_index(
            "ix_reconciliation_marks_shop_source", ["shop_id", "source_type", "source_id"], unique=False
        )

    with op.batch_alter_table("expense_categories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "cash_flow_class",
                sa.Enum(
                    "OPERATING",
                    "INVESTING",
                    "FINANCING",
                    "OTHER",
                    name="cash_flow_class",
                    native_enum=False,
                    create_constraint=True,
                ),
                nullable=True,
            )
        )

    _create_triggers()
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                f"SELECT id, '{code}' FROM roles WHERE shop_id IS NULL AND code = '{role_code}'"
            )


def downgrade() -> None:
    _drop_triggers()
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "DELETE FROM role_permissions WHERE permission = "
                f"'{code}' AND role_id IN (SELECT id FROM roles WHERE shop_id IS NULL AND code = '{role_code}')"
            )
    op.drop_table("expenses")
    with op.batch_alter_table("expense_categories") as batch_op:
        batch_op.drop_constraint("cash_flow_class", type_="check")
        batch_op.drop_column("cash_flow_class")
    for table in (
        "reconciliation_marks",
        "financial_periods",
        "finance_entries",
        "cash_counts",
        "tax_rates",
        "tax_settings",
        "finance_settings",
    ):
        op.drop_table(table)
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
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.create_index("ix_expenses_shop_date", ["shop_id", "expense_date"], unique=False)
