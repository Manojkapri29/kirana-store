"""Offline synchronisation (Phase 18)

* `sync_operations`: one row per operation a device queued offline; (shop, client_op_id) is unique and is the idempotency record.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-24 12:40:49.338327
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_operations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("client_op_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("op_type", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SYNCED",
                "CONFLICT",
                "FAILED",
                "DISCARDED",
                name="sync_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("message", sa.String(length=300), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(client_op_id)) > 0", name=op.f("ck_sync_operations_client_op_id_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_sync_operations_shop_id_user_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_sync_operations_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_operations")),
        sa.UniqueConstraint("shop_id", "client_op_id", name=op.f("uq_sync_operations_shop_id_client_op_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_sync_operations_shop_id_id")),
    )
    with op.batch_alter_table("sync_operations", schema=None) as batch_op:
        batch_op.create_index("ix_sync_operations_shop_status", ["shop_id", "status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sync_operations", schema=None) as batch_op:
        batch_op.drop_index("ix_sync_operations_shop_status")

    op.drop_table("sync_operations")
