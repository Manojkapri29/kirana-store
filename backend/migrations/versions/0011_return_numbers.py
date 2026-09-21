"""return numbers: every sales return and purchase return gets a shop-scoped document number

`sales_returns.return_no` (SRT/2026-27/0001) and `purchase_returns.return_no` (PRT/2026-27/0001). A return exists
only once it is posted, so the number is required. Any return that already exists is numbered `SRT/LEGACY/<id>`
or `PRT/LEGACY/<id>`. Self-contained: no imports from `app`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (("sales_returns", "SRT"), ("purchase_returns", "PRT"))


def upgrade() -> None:
    for table, prefix in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("return_no", sa.String(length=30), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET return_no = '{prefix}/LEGACY/' || CAST(id AS VARCHAR(20))"))
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("return_no", existing_type=sa.String(length=30), nullable=False)
            batch_op.create_unique_constraint(op.f(f"uq_{table}_shop_id_return_no"), ["shop_id", "return_no"])
            batch_op.create_check_constraint(
                op.f(f"ck_{table}_return_no_not_blank"), "length(trim(return_no)) > 0"
            )


def downgrade() -> None:
    for table, _ in reversed(TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(op.f(f"ck_{table}_return_no_not_blank"), type_="check")
            batch_op.drop_constraint(op.f(f"uq_{table}_shop_id_return_no"), type_="unique")
            batch_op.drop_column("return_no")
