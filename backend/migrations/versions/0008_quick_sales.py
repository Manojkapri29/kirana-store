"""quick sales: draft/posted/void lifecycle, numbers, an optional transaction discount, posting details

Turns the Phase 2 `quick_sales` table into a working workflow, the same way 0006 did for `sales`:

* `status` gains DRAFT and no longer defaults to POSTED.
* `quick_no` (the number given when posted), `posted_at`, `posted_by`.
* `gross_amount` and `discount`: an optional discount off the whole entry; `total_amount = gross_amount -
  discount`. Existing rows get `gross_amount = total_amount` and `discount = 0`.
* `payment_type` and `amount_paid` become nullable (a draft has no payment yet); a numbered quick sale must
  have them. Existing rows were posted, so they are numbered `QS/LEGACY/<id>`.

The table still has NO product, quantity or cost columns: that is deliberate and tested.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("quick_sales") as batch_op:
        batch_op.add_column(sa.Column("quick_no", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("gross_amount", sa.BigInteger(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("discount", sa.BigInteger(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("posted_by", ID, nullable=True))
    # Plain statements (no Python loop) so this also renders when generating SQL without a database.
    op.execute(
        sa.text(
            "UPDATE quick_sales SET gross_amount = total_amount, posted_at = created_at, posted_by = created_by,"
            " quick_no = 'QS/LEGACY/' || CAST(id AS VARCHAR(20))"
        )
    )

    with op.batch_alter_table("quick_sales") as batch_op:
        batch_op.drop_constraint(op.f("ck_quick_sales_status"), type_="check")
        batch_op.drop_constraint(op.f("ck_quick_sales_void_needs_reason"), type_="check")
        # The column stays VARCHAR(6): it already fits DRAFT. Only its default and its CHECK change.
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default=None
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_status"), "status IN ('DRAFT', 'POSTED', 'VOID')"
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_void_needs_reason"), "status <> 'VOID' OR void_reason IS NOT NULL"
        )
        batch_op.alter_column("payment_type", existing_type=sa.String(length=6), nullable=True)
        batch_op.alter_column("amount_paid", existing_type=sa.BigInteger(), nullable=True)
        batch_op.create_check_constraint(op.f("ck_quick_sales_gross_amount_positive"), "gross_amount > 0")
        batch_op.create_check_constraint(op.f("ck_quick_sales_discount_non_negative"), "discount >= 0")
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_total_is_gross_less_discount"), "total_amount = gross_amount - discount"
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_quick_no_not_blank"), "length(trim(quick_no)) > 0"
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_number_and_posted_at_together"),
            "(quick_no IS NULL AND posted_at IS NULL) OR (quick_no IS NOT NULL AND posted_at IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_posted_needs_number"), "status <> 'POSTED' OR quick_no IS NOT NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_draft_has_no_number"), "status <> 'DRAFT' OR quick_no IS NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_posted_has_payment"),
            "quick_no IS NULL OR (payment_type IS NOT NULL AND amount_paid IS NOT NULL)",
        )
        batch_op.create_unique_constraint(op.f("uq_quick_sales_shop_id_quick_no"), ["shop_id", "quick_no"])
        batch_op.create_foreign_key(
            op.f("fk_quick_sales_shop_id_posted_by"), "users", ["shop_id", "posted_by"], ["shop_id", "id"]
        )


def downgrade() -> None:
    if not context.is_offline_mode():  # offline (SQL-only) mode has no database to look at
        drafts = (
            op.get_bind().execute(sa.text("SELECT count(*) FROM quick_sales WHERE status = 'DRAFT'")).scalar()
        )
        if drafts:
            raise RuntimeError(
                f"{drafts} draft quick sale(s) exist. Post or discard them before downgrading: "
                "revision 0007 has no DRAFT status."
            )

    with op.batch_alter_table("quick_sales") as batch_op:
        batch_op.drop_constraint("fk_quick_sales_shop_id_posted_by", type_="foreignkey")
        batch_op.drop_constraint("uq_quick_sales_shop_id_quick_no", type_="unique")
        for name in (
            "posted_has_payment",
            "draft_has_no_number",
            "posted_needs_number",
            "number_and_posted_at_together",
            "quick_no_not_blank",
            "total_is_gross_less_discount",
            "discount_non_negative",
            "gross_amount_positive",
        ):
            batch_op.drop_constraint(op.f(f"ck_quick_sales_{name}"), type_="check")
        batch_op.alter_column("amount_paid", existing_type=sa.BigInteger(), nullable=False)
        batch_op.alter_column("payment_type", existing_type=sa.String(length=6), nullable=False)
        batch_op.drop_constraint(op.f("ck_quick_sales_void_needs_reason"), type_="check")
        batch_op.drop_constraint(op.f("ck_quick_sales_status"), type_="check")
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default="POSTED"
        )
        batch_op.create_check_constraint(op.f("ck_quick_sales_status"), "status IN ('POSTED', 'VOID')")
        batch_op.create_check_constraint(
            op.f("ck_quick_sales_void_needs_reason"), "status = 'POSTED' OR void_reason IS NOT NULL"
        )
        batch_op.drop_column("posted_by")
        batch_op.drop_column("posted_at")
        batch_op.drop_column("discount")
        batch_op.drop_column("gross_amount")
        batch_op.drop_column("quick_no")
