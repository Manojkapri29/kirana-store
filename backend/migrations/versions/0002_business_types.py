"""business types: make the platform generic, not grocery-only

* `business_types`: reference table of kinds of business (grocery, bakery, garments, ...). Adding a kind
  later is a data migration (an INSERT), not a schema change.
* `shops.business_type`: which kind of business each shop is. Existing shops become GROCERY, because every
  shop created before this migration was a kirana/grocery shop. The column has no default afterwards, so a
  new shop must state its type explicitly.
* `units`: adds metre, pair, bottle and tray, so garments, footwear, cloth, drinks and bakery trays can be
  counted naturally. Units stay shared and no shop is restricted to a subset.

Self-contained: plain SQLAlchemy only, no imports from `app`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, English name, sort order)
BUSINESS_TYPES = [
    ("GROCERY", "Grocery / Kirana", 10),
    ("GENERAL_STORE", "General Store", 20),
    ("SWEET_SHOP", "Sweet Shop / Halwai", 30),
    ("BAKERY", "Bakery", 40),
    ("DAIRY", "Dairy", 50),
    ("FRUIT", "Fruit Shop", 60),
    ("VEGETABLE", "Vegetable Vendor", 70),
    ("MEAT_FOOD", "Meat / Food Shop", 80),
    ("GARMENTS", "Garments", 90),
    ("FOOTWEAR", "Footwear", 100),
    ("COSMETICS", "Cosmetics", 110),
    ("ELECTRONICS", "Electronics / Mobile Shop", 120),
    ("HARDWARE", "Hardware", 130),
    ("STATIONERY", "Stationery", 140),
    ("OTHER", "Other", 999),
]

# (code, name, allows_decimal)
NEW_UNITS = [
    ("m", "Metre", True),
    ("pair", "Pair", False),
    ("btl", "Bottle", False),
    ("tray", "Tray", False),
]

# Every shop that exists when this migration runs was created as a kirana/grocery shop.
EXISTING_SHOPS_BECOME = "GROCERY"


def upgrade() -> None:
    op.create_table(
        "business_types",
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_business_types_code_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_business_types_name_not_blank")),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_business_types")),
    )
    business_types = sa.table(
        "business_types",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        business_types,
        [{"code": code, "name": name, "sort_order": order} for code, name, order in BUSINESS_TYPES],
    )

    units = sa.table(
        "units",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("allows_decimal", sa.Boolean),
    )
    op.bulk_insert(
        units, [{"code": code, "name": name, "allows_decimal": decimal} for code, name, decimal in NEW_UNITS]
    )

    # Step 1: add the column, backfilling existing shops through a temporary default.
    # (On SQLite this rebuilds the table; on PostgreSQL it is a plain ALTER TABLE.)
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(
            sa.Column(
                "business_type", sa.String(length=30), server_default=EXISTING_SHOPS_BECOME, nullable=False
            )
        )
        batch_op.create_foreign_key(
            op.f("fk_shops_business_type"), "business_types", ["business_type"], ["code"]
        )
    # Step 2: remove the default, so every future shop has to choose its type on purpose.
    with op.batch_alter_table("shops") as batch_op:
        batch_op.alter_column(
            "business_type",
            existing_type=sa.String(length=30),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_constraint(op.f("fk_shops_business_type"), type_="foreignkey")
        batch_op.drop_column("business_type")
    op.drop_table("business_types")
    # Fails loudly if a product already uses one of these units, which is the safe outcome.
    op.execute(sa.text("DELETE FROM units WHERE code IN ('m', 'pair', 'btl', 'tray')"))
