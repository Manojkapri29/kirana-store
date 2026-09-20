"""suppliers: alternate phone, email and notes, plus search indexes

Adds three optional columns to `suppliers` and two indexes (supplier search by name, and "which products use
this supplier"). Existing suppliers keep all their data; the new columns are NULL. Adding nullable columns
and indexes needs no table rebuild on SQLite.

Self-contained: plain SQLAlchemy only, no imports from `app`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("alternate_phone", sa.String(length=20), nullable=True))
    op.add_column("suppliers", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("suppliers", sa.Column("notes", sa.Text(), nullable=True))
    op.create_index("ix_suppliers_shop_id_name", "suppliers", ["shop_id", "name"])
    op.create_index("ix_products_shop_id_default_supplier_id", "products", ["shop_id", "default_supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_products_shop_id_default_supplier_id", table_name="products")
    op.drop_index("ix_suppliers_shop_id_name", table_name="suppliers")
    # SQLite rebuilds the table to drop columns; the migration environment handles that safely.
    with op.batch_alter_table("suppliers") as batch_op:
        batch_op.drop_column("notes")
        batch_op.drop_column("email")
        batch_op.drop_column("alternate_phone")
