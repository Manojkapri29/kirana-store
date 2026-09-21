"""product images and the image_intelligence plan feature

* `product_images`: the one photo a shop chose to keep for a product. Metadata only (hash, type, size, dimensions,
  the private storage key); the file is in the image store, never in the database, and is only served to its own
  shop. Nothing creates a row from an analysis: the user must confirm and ask to keep the photo.
* `image_intelligence`: a new plan feature. Existing plans get a row: on for `pro`, off for the others, so nothing
  that worked before changes and no shop suddenly sends photos anywhere.

Self-contained: no imports from `app`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_images",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("product_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("height > 0", name=op.f("ck_product_images_height_positive")),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_product_images_sha256_is_64_chars")),
        sa.CheckConstraint(
            "length(trim(storage_key)) > 0", name=op.f("ck_product_images_storage_key_not_blank")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_product_images_size_bytes_positive")),
        sa.CheckConstraint("width > 0", name=op.f("ck_product_images_width_positive")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_product_images_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_product_images_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_product_images_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_images")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_product_images_shop_id_id")),
        sa.UniqueConstraint("shop_id", "product_id", name=op.f("uq_product_images_shop_id_product_id")),
    )
    op.execute(
        sa.text(
            "INSERT INTO plan_features (plan_id, feature_key, enabled) "
            "SELECT id, 'image_intelligence', CASE WHEN code = 'pro' THEN :on ELSE :off END FROM plans "
            "WHERE NOT EXISTS (SELECT 1 FROM plan_features f WHERE f.plan_id = plans.id "
            "AND f.feature_key = 'image_intelligence')"
        ).bindparams(on=True, off=False)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM plan_features WHERE feature_key = 'image_intelligence'"))
    op.drop_table("product_images")
