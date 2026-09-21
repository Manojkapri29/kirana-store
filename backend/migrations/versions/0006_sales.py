"""sales: draft/posted/void lifecycle, bill discount, posting details, sale-line units

Turns the Phase 2 sale tables into a working Detailed Sale workflow:

* `sales.status` gains DRAFT (a cart; it never touches stock or khata) and no longer defaults to POSTED.
* `sales.invoice_no`, `payment_type` and `amount_paid` become nullable, because a draft has no number and no
  payment yet. New checks keep them mandatory for every non-draft sale.
* New columns: `subtotal`, `discount` (an amount off the whole bill), `posted_at`, `posted_by`. The new rule
  `total_amount = subtotal - discount` ties them together. Existing sales get `subtotal = total_amount`,
  `discount = 0`, and `posted_at = created_at`.
* `sale_items.unit_id`: the unit the quantity is counted in, filled from the product for existing rows.

SQLite rebuilds a table to change constraints; the migration environment turns foreign keys off for that and
verifies afterwards. PostgreSQL uses plain ALTER TABLE. Self-contained: no imports from `app`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    # ---- sales: new columns first, so existing rows can be filled in before rules are tightened ----
    with op.batch_alter_table("sales") as batch_op:
        batch_op.add_column(sa.Column("subtotal", sa.BigInteger(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("discount", sa.BigInteger(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("posted_by", ID, nullable=True))
    # Plain statements (no Python loop) so this also renders when generating SQL without a database.
    op.execute(
        sa.text("UPDATE sales SET subtotal = total_amount, posted_at = created_at, posted_by = created_by")
    )

    with op.batch_alter_table("sales") as batch_op:
        batch_op.drop_constraint(op.f("ck_sales_status"), type_="check")
        batch_op.drop_constraint(op.f("ck_sales_void_needs_reason"), type_="check")
        # The column stays VARCHAR(6): it already fits DRAFT. Only its default and its CHECK change.
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default=None
        )
        batch_op.create_check_constraint(op.f("ck_sales_status"), "status IN ('DRAFT', 'POSTED', 'VOID')")
        batch_op.create_check_constraint(
            op.f("ck_sales_void_needs_reason"), "status <> 'VOID' OR void_reason IS NOT NULL"
        )
        batch_op.alter_column("invoice_no", existing_type=sa.String(length=30), nullable=True)
        batch_op.alter_column("payment_type", existing_type=sa.String(length=6), nullable=True)
        batch_op.alter_column("amount_paid", existing_type=sa.BigInteger(), nullable=True)
        batch_op.create_check_constraint(op.f("ck_sales_subtotal_non_negative"), "subtotal >= 0")
        batch_op.create_check_constraint(op.f("ck_sales_discount_non_negative"), "discount >= 0")
        batch_op.create_check_constraint(
            op.f("ck_sales_total_is_subtotal_less_discount"), "total_amount = subtotal - discount"
        )
        batch_op.create_check_constraint(
            op.f("ck_sales_number_and_posted_at_together"),
            "(invoice_no IS NULL AND posted_at IS NULL) OR (invoice_no IS NOT NULL AND posted_at IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_sales_posted_needs_number"), "status <> 'POSTED' OR invoice_no IS NOT NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_sales_draft_has_no_number"), "status <> 'DRAFT' OR invoice_no IS NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_sales_posted_has_payment"),
            "invoice_no IS NULL OR (payment_type IS NOT NULL AND amount_paid IS NOT NULL)",
        )
        batch_op.create_foreign_key(
            op.f("fk_sales_shop_id_posted_by"), "users", ["shop_id", "posted_by"], ["shop_id", "id"]
        )

    # ---- sale_items ----
    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.add_column(sa.Column("unit_id", ID, nullable=True))
    op.execute(
        sa.text(
            "UPDATE sale_items SET unit_id = "
            "(SELECT products.unit_id FROM products WHERE products.id = sale_items.product_id)"
        )
    )
    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.alter_column("unit_id", existing_type=ID, nullable=False)
        batch_op.create_foreign_key(op.f("fk_sale_items_unit_id"), "units", ["unit_id"], ["id"])


def downgrade() -> None:
    if not context.is_offline_mode():  # offline (SQL-only) mode has no database to look at
        drafts = op.get_bind().execute(sa.text("SELECT count(*) FROM sales WHERE status = 'DRAFT'")).scalar()
        if drafts:
            raise RuntimeError(
                f"{drafts} draft sale(s) exist. Post or discard them before downgrading: "
                "revision 0005 has no DRAFT status."
            )

    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.drop_constraint("fk_sale_items_unit_id", type_="foreignkey")
        batch_op.drop_column("unit_id")

    with op.batch_alter_table("sales") as batch_op:
        batch_op.drop_constraint("fk_sales_shop_id_posted_by", type_="foreignkey")
        for name in (
            "posted_has_payment",
            "draft_has_no_number",
            "posted_needs_number",
            "number_and_posted_at_together",
            "total_is_subtotal_less_discount",
            "discount_non_negative",
            "subtotal_non_negative",
        ):
            batch_op.drop_constraint(op.f(f"ck_sales_{name}"), type_="check")
        batch_op.alter_column("amount_paid", existing_type=sa.BigInteger(), nullable=False)
        batch_op.alter_column("payment_type", existing_type=sa.String(length=6), nullable=False)
        batch_op.alter_column("invoice_no", existing_type=sa.String(length=30), nullable=False)
        batch_op.drop_constraint(op.f("ck_sales_void_needs_reason"), type_="check")
        batch_op.drop_constraint(op.f("ck_sales_status"), type_="check")
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default="POSTED"
        )
        batch_op.create_check_constraint(op.f("ck_sales_status"), "status IN ('POSTED', 'VOID')")
        batch_op.create_check_constraint(
            op.f("ck_sales_void_needs_reason"), "status = 'POSTED' OR void_reason IS NOT NULL"
        )
        batch_op.drop_column("posted_by")
        batch_op.drop_column("posted_at")
        batch_op.drop_column("discount")
        batch_op.drop_column("subtotal")
