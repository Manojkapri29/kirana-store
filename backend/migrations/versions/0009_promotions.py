"""promotions: one generic discount system, snapshots on the sale, and the new total rule

* `promotions`: what a shop offers (percentage, fixed amount, offer price, buy X get Y; whole bill, chosen
  products or categories; optional coupon code; audience; date window; usage limits; priority and stacking).
* `sale_promotions`: what a POSTED sale actually got, frozen at posting (name, terms, amount, reason, coupon).
  Invoices read this, so editing or pausing a promotion never changes history.
* `sales.promotion_discount`, `sales.coupon_code`, `sale_items.promotion_discount`. The bill total rule becomes
  `total = subtotal - discount - promotion_discount` (every existing sale has 0 promotion discount, so every
  existing total still satisfies it), and a line's promotion share can never exceed the line.

Existing data is untouched. SQLite rebuilds a table to change constraints; the migration environment turns
foreign keys off for that and verifies afterwards. Self-contained: no imports from `app`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "promotions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "promo_type",
            sa.Enum(
                "PERCENT",
                "AMOUNT",
                "OFFER_PRICE",
                "BUY_X_GET_Y",
                name="promo_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.Enum(
                "CART", "PRODUCTS", "CATEGORIES", name="scope", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ACTIVE",
                "PAUSED",
                "EXPIRED",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stackable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coupon_code", sa.String(length=40), nullable=True),
        sa.Column(
            "audience",
            sa.Enum(
                "ALL", "NEW_CUSTOMER", "CUSTOMERS", name="audience", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("percent_bp", sa.Integer(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("offer_price", sa.BigInteger(), nullable=True),
        sa.Column("buy_quantity", sa.Integer(), nullable=True),
        sa.Column("get_quantity", sa.Integer(), nullable=True),
        sa.Column("get_percent_bp", sa.Integer(), nullable=True),
        sa.Column("min_cart_value", sa.BigInteger(), nullable=True),
        sa.Column("min_quantity", sa.BigInteger(), nullable=True),
        sa.Column("max_discount", sa.BigInteger(), nullable=True),
        sa.Column("usage_limit", sa.Integer(), nullable=True),
        sa.Column("per_customer_limit", sa.Integer(), nullable=True),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(promo_type = 'PERCENT' AND percent_bp IS NOT NULL AND amount IS NULL AND offer_price IS NULL AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent_bp IS NULL) OR (promo_type = 'AMOUNT' AND percent_bp IS NULL AND amount IS NOT NULL AND offer_price IS NULL AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent_bp IS NULL) OR (promo_type = 'OFFER_PRICE' AND percent_bp IS NULL AND amount IS NULL AND offer_price IS NOT NULL AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent_bp IS NULL) OR (promo_type = 'BUY_X_GET_Y' AND percent_bp IS NULL AND amount IS NULL AND offer_price IS NULL AND buy_quantity IS NOT NULL AND get_quantity IS NOT NULL AND get_percent_bp IS NOT NULL)",
            name=op.f("ck_promotions_benefit_matches_type"),
        ),
        sa.CheckConstraint(
            "promo_type NOT IN ('OFFER_PRICE', 'BUY_X_GET_Y') OR scope <> 'CART'",
            name=op.f("ck_promotions_type_needs_items"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_promotions_amount_positive")),
        sa.CheckConstraint("buy_quantity > 0", name=op.f("ck_promotions_buy_quantity_positive")),
        sa.CheckConstraint(
            "get_percent_bp BETWEEN 1 AND 10000", name=op.f("ck_promotions_get_percent_bp_range")
        ),
        sa.CheckConstraint("get_quantity > 0", name=op.f("ck_promotions_get_quantity_positive")),
        sa.CheckConstraint("length(trim(coupon_code)) > 0", name=op.f("ck_promotions_coupon_code_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_promotions_name_not_blank")),
        sa.CheckConstraint("max_discount > 0", name=op.f("ck_promotions_max_discount_positive")),
        sa.CheckConstraint("min_cart_value >= 0", name=op.f("ck_promotions_min_cart_value_non_negative")),
        sa.CheckConstraint("min_quantity > 0", name=op.f("ck_promotions_min_quantity_positive")),
        sa.CheckConstraint("offer_price >= 0", name=op.f("ck_promotions_offer_price_non_negative")),
        sa.CheckConstraint("per_customer_limit > 0", name=op.f("ck_promotions_per_customer_limit_positive")),
        sa.CheckConstraint("percent_bp BETWEEN 1 AND 10000", name=op.f("ck_promotions_percent_bp_range")),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name=op.f("ck_promotions_window_is_ordered"),
        ),
        sa.CheckConstraint("usage_limit > 0", name=op.f("ck_promotions_usage_limit_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_promotions_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_promotions_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotions")),
        sa.UniqueConstraint("shop_id", "coupon_code", name=op.f("uq_promotions_shop_id_coupon_code")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_promotions_shop_id_id")),
    )
    with op.batch_alter_table("promotions", schema=None) as batch_op:
        batch_op.create_index("ix_promotions_shop_status", ["shop_id", "status"], unique=False)

    op.create_table(
        "sale_promotions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sale_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("promotion_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "promo_type",
            sa.Enum(
                "PERCENT",
                "AMOUNT",
                "OFFER_PRICE",
                "BUY_X_GET_Y",
                name="promo_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("terms", sa.String(length=200), nullable=False),
        sa.Column("coupon_code", sa.String(length=40), nullable=True),
        sa.Column("discount_amount", sa.BigInteger(), nullable=False),
        sa.Column("basis", sa.String(length=300), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("discount_amount > 0", name=op.f("ck_sale_promotions_discount_amount_positive")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_sale_promotions_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["shop_id", "promotion_id"],
            ["promotions.shop_id", "promotions.id"],
            name=op.f("fk_sale_promotions_shop_id_promotion_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sale_id"],
            ["sales.shop_id", "sales.id"],
            name=op.f("fk_sale_promotions_shop_id_sale_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sale_promotions_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_promotions")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sale_promotions_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "sale_id", "promotion_id", name=op.f("uq_sale_promotions_shop_id_sale_id_promotion_id")
        ),
    )
    with op.batch_alter_table("sale_promotions", schema=None) as batch_op:
        batch_op.create_index("ix_sale_promotions_shop_promotion", ["shop_id", "promotion_id"], unique=False)
        batch_op.create_index("ix_sale_promotions_shop_sale", ["shop_id", "sale_id"], unique=False)

    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.add_column(
            sa.Column("promotion_discount", sa.BigInteger(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_sale_items_promotion_discount_non_negative"), "promotion_discount >= 0"
        )
        batch_op.create_check_constraint(
            op.f("ck_sale_items_promotion_within_line"), "promotion_discount <= line_total"
        )

    with op.batch_alter_table("sales") as batch_op:
        batch_op.add_column(
            sa.Column("promotion_discount", sa.BigInteger(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("coupon_code", sa.String(length=40), nullable=True))
    with op.batch_alter_table("sales") as batch_op:
        batch_op.drop_constraint(op.f("ck_sales_total_is_subtotal_less_discount"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_sales_promotion_discount_non_negative"), "promotion_discount >= 0"
        )
        batch_op.create_check_constraint(
            op.f("ck_sales_total_is_subtotal_less_discounts"),
            "total_amount = subtotal - discount - promotion_discount",
        )


def downgrade() -> None:
    # Rows that used a promotion cannot be expressed in the old total rule, so refuse rather than lose money.
    if not context.is_offline_mode():
        used = (
            op.get_bind().execute(sa.text("SELECT count(*) FROM sales WHERE promotion_discount > 0")).scalar()
        )
        if used:
            raise RuntimeError(
                f"{used} sale(s) have a promotion discount. Revision 0008 cannot represent them, so the "
                "downgrade is refused to keep every total correct."
            )

    with op.batch_alter_table("sales") as batch_op:
        batch_op.drop_constraint(op.f("ck_sales_total_is_subtotal_less_discounts"), type_="check")
        batch_op.drop_constraint(op.f("ck_sales_promotion_discount_non_negative"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_sales_total_is_subtotal_less_discount"), "total_amount = subtotal - discount"
        )
        batch_op.drop_column("coupon_code")
        batch_op.drop_column("promotion_discount")

    with op.batch_alter_table("sale_items") as batch_op:
        batch_op.drop_constraint(op.f("ck_sale_items_promotion_within_line"), type_="check")
        batch_op.drop_constraint(op.f("ck_sale_items_promotion_discount_non_negative"), type_="check")
        batch_op.drop_column("promotion_discount")

    op.drop_index("ix_sale_promotions_shop_sale", table_name="sale_promotions")
    op.drop_index("ix_sale_promotions_shop_promotion", table_name="sale_promotions")
    op.drop_table("sale_promotions")
    op.drop_index("ix_promotions_shop_status", table_name="promotions")
    op.drop_table("promotions")
