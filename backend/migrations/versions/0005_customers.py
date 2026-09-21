"""customers: optional email and a name index for search

Adds one optional column (`customers.email`) and one index (customer search and ordering by name). Existing
customers keep all their data; the new column is NULL. Adding a nullable column and an index needs no table
rebuild on SQLite, and the insert-only ledger triggers on `customer_ledger` are not touched.

Self-contained: plain SQLAlchemy only, no imports from `app`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("email", sa.String(length=254), nullable=True))
    op.create_index("ix_customers_shop_id_name", "customers", ["shop_id", "name"])


def downgrade() -> None:
    op.drop_index("ix_customers_shop_id_name", table_name="customers")
    # SQLite rebuilds the table to drop a column; the migration environment handles that safely.
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_column("email")
