"""purchases: draft/posted/void lifecycle, purchase numbers, line discounts, cost snapshots

Turns the Phase 2 purchase tables into a working purchase workflow:

* `purchases.status` gains DRAFT (a purchase being prepared; it never touches stock).
* `purchases.purchase_no`, `posted_at`, `posted_by`: the shop-scoped number given when a purchase is
  posted, and who/when. Any purchase that already exists as POSTED is numbered `PUR/LEGACY/<id>`.
* The supplier-invoice uniqueness rule now ignores VOID purchases, so a corrected copy of a voided
  purchase can reuse the supplier's invoice number. (A partial unique index; SQLite and PostgreSQL both
  support it.)
* `purchase_items`: `unit_id` (the unit the quantity is counted in, filled from the product for any
  existing row), `discount`, and a posting snapshot: `stock_before`, `avg_cost_before`, `avg_cost_after`.

SQLite rebuilds a table to change constraints; the migration environment turns foreign keys off for that and
verifies afterwards. PostgreSQL uses plain ALTER TABLE. Self-contained: no imports from `app`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    # ---- purchases: new columns first, so existing rows can be filled in before rules are tightened ----
    with op.batch_alter_table("purchases") as batch_op:
        batch_op.add_column(sa.Column("purchase_no", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("posted_by", ID, nullable=True))

    # One plain statement (no Python loop) so it also renders when generating SQL without a database.
    op.execute(
        sa.text(
            "UPDATE purchases SET purchase_no = 'PUR/LEGACY/' || CAST(id AS VARCHAR(20)), "
            "posted_at = created_at WHERE status = 'POSTED'"
        )
    )

    with op.batch_alter_table("purchases") as batch_op:
        # DRAFT joins the allowed statuses; a purchase no longer defaults to POSTED (it starts as a draft).
        batch_op.drop_constraint(op.f("ck_purchases_status"), type_="check")
        # The column stays VARCHAR(6): it already fits DRAFT. Only its default and its CHECK change.
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default=None
        )
        batch_op.create_check_constraint(op.f("ck_purchases_status"), "status IN ('DRAFT', 'POSTED', 'VOID')")
        # A draft has no void reason, so the rule becomes "a VOID purchase needs a reason".
        batch_op.drop_constraint(op.f("ck_purchases_void_needs_reason"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_purchases_void_needs_reason"), "status <> 'VOID' OR void_reason IS NOT NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_purchases_number_and_posted_at_together"),
            "(purchase_no IS NULL AND posted_at IS NULL) OR (purchase_no IS NOT NULL AND posted_at IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_purchases_posted_needs_number"), "status <> 'POSTED' OR purchase_no IS NOT NULL"
        )
        batch_op.create_check_constraint(
            op.f("ck_purchases_draft_has_no_number"), "status <> 'DRAFT' OR purchase_no IS NULL"
        )
        batch_op.create_unique_constraint(
            op.f("uq_purchases_shop_id_purchase_no"), ["shop_id", "purchase_no"]
        )
        batch_op.create_foreign_key(
            op.f("fk_purchases_shop_id_posted_by"), "users", ["shop_id", "posted_by"], ["shop_id", "id"]
        )
        # Supplier invoice numbers: unique among live purchases, released when a purchase is voided.
        batch_op.drop_constraint("uq_purchases_shop_id_supplier_id_supplier_invoice_no", type_="unique")

    op.create_index(
        "uq_purchases_supplier_invoice_active",
        "purchases",
        ["shop_id", "supplier_id", "supplier_invoice_no"],
        unique=True,
        sqlite_where=sa.text("status <> 'VOID'"),
        postgresql_where=sa.text("status <> 'VOID'"),
    )
    op.create_index(
        "ix_purchases_shop_supplier_date", "purchases", ["shop_id", "supplier_id", "purchase_date"]
    )

    # ---- purchase_items ----
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.add_column(sa.Column("unit_id", ID, nullable=True))
        batch_op.add_column(sa.Column("discount", sa.BigInteger(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("stock_before", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("avg_cost_before", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("avg_cost_after", sa.BigInteger(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE purchase_items SET unit_id = "
            "(SELECT products.unit_id FROM products WHERE products.id = purchase_items.product_id)"
        )
    )
    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.alter_column("unit_id", existing_type=ID, nullable=False)
        batch_op.create_foreign_key(op.f("fk_purchase_items_unit_id"), "units", ["unit_id"], ["id"])
        batch_op.create_check_constraint(op.f("ck_purchase_items_discount_non_negative"), "discount >= 0")
        batch_op.create_check_constraint(
            op.f("ck_purchase_items_avg_cost_before_non_negative"), "avg_cost_before >= 0"
        )
        batch_op.create_check_constraint(
            op.f("ck_purchase_items_avg_cost_after_non_negative"), "avg_cost_after >= 0"
        )


def downgrade() -> None:
    if not context.is_offline_mode():  # offline (SQL-only) mode has no database to look at
        drafts = (
            op.get_bind().execute(sa.text("SELECT count(*) FROM purchases WHERE status = 'DRAFT'")).scalar()
        )
        if drafts:
            raise RuntimeError(
                f"{drafts} draft purchase(s) exist. Post or discard them before downgrading: "
                "revision 0003 has no DRAFT status."
            )

    with op.batch_alter_table("purchase_items") as batch_op:
        batch_op.drop_constraint(op.f("ck_purchase_items_avg_cost_after_non_negative"), type_="check")
        batch_op.drop_constraint(op.f("ck_purchase_items_avg_cost_before_non_negative"), type_="check")
        batch_op.drop_constraint(op.f("ck_purchase_items_discount_non_negative"), type_="check")
        batch_op.drop_constraint("fk_purchase_items_unit_id", type_="foreignkey")
        batch_op.drop_column("avg_cost_after")
        batch_op.drop_column("avg_cost_before")
        batch_op.drop_column("stock_before")
        batch_op.drop_column("discount")
        batch_op.drop_column("unit_id")

    op.drop_index("ix_purchases_shop_supplier_date", table_name="purchases")
    op.drop_index("uq_purchases_supplier_invoice_active", table_name="purchases")
    with op.batch_alter_table("purchases") as batch_op:
        batch_op.create_unique_constraint(
            op.f("uq_purchases_shop_id_supplier_id_supplier_invoice_no"),
            ["shop_id", "supplier_id", "supplier_invoice_no"],
        )
        batch_op.drop_constraint("fk_purchases_shop_id_posted_by", type_="foreignkey")
        batch_op.drop_constraint("uq_purchases_shop_id_purchase_no", type_="unique")
        batch_op.drop_constraint(op.f("ck_purchases_draft_has_no_number"), type_="check")
        batch_op.drop_constraint(op.f("ck_purchases_posted_needs_number"), type_="check")
        batch_op.drop_constraint(op.f("ck_purchases_number_and_posted_at_together"), type_="check")
        batch_op.drop_constraint(op.f("ck_purchases_void_needs_reason"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_purchases_void_needs_reason"), "status = 'POSTED' OR void_reason IS NOT NULL"
        )
        batch_op.drop_constraint(op.f("ck_purchases_status"), type_="check")
        batch_op.alter_column(
            "status", existing_type=sa.String(length=6), existing_nullable=False, server_default="POSTED"
        )
        batch_op.create_check_constraint(op.f("ck_purchases_status"), "status IN ('POSTED', 'VOID')")
        batch_op.drop_column("posted_by")
        batch_op.drop_column("posted_at")
        batch_op.drop_column("purchase_no")
