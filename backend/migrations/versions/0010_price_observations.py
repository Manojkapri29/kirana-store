"""price observations: what outside sources said a product costs, kept per shop

One append-only table that is both a shop's price-check history and its cache. Rows are information only: no
product price or cost is ever changed from them. Self-contained: no imports from `app`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_observations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=True),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("pack_text", sa.String(length=50), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("location_text", sa.String(length=300), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("observed_on", sa.Date(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(currency) = 3", name=op.f("ck_price_observations_currency_is_three_letters")
        ),
        sa.CheckConstraint("length(trim(barcode)) > 0", name=op.f("ck_price_observations_barcode_not_blank")),
        sa.CheckConstraint(
            "length(trim(provider)) > 0", name=op.f("ck_price_observations_provider_not_blank")
        ),
        sa.CheckConstraint("price > 0", name=op.f("ck_price_observations_price_positive")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_price_observations_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_observations")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_price_observations_shop_id_id")),
    )
    with op.batch_alter_table("price_observations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_price_observations_shop_barcode",
            ["shop_id", "barcode", "provider", "checked_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("price_observations", schema=None) as batch_op:
        batch_op.drop_index("ix_price_observations_shop_barcode")

    op.drop_table("price_observations")
