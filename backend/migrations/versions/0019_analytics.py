"""Advanced reporting and business intelligence (Phase 16)

* Seeds the analytics permissions into the system roles.
* `saved_reports`: custom report definitions (JSON, validated against the reporting allowlist; archived, never deleted).
* `scheduled_reports`: params / export_format / delivery_channel / recipients for scheduled advanced reports.
* `report_runs`: one row per scheduled run; (schedule, run_key) is unique, which makes scheduled runs idempotent.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = [
    "ANALYTICS_VIEW",
    "ANALYTICS_ADVANCED",
    "ANALYTICS_EXECUTIVE",
    "ANALYTICS_CUSTOM_REPORT",
    "ANALYTICS_EXPORT",
    "ANALYTICS_SCHEDULE",
]
NEW_PERMISSIONS = {
    "OWNER": ALL,
    "MANAGER": ALL,
    "ACCOUNTANT": ["ANALYTICS_VIEW", "ANALYTICS_ADVANCED", "ANALYTICS_EXPORT"],
}


ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "saved_reports",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("dataset", sa.String(30), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("updated_by", ID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_reports")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_saved_reports_shop_id_id")),
        sa.UniqueConstraint("shop_id", "name", name=op.f("uq_saved_reports_shop_id_name")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_saved_reports_shop_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_saved_reports_shop_id_created_by"),
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_saved_reports_name_not_blank")),
        sa.CheckConstraint("length(trim(dataset)) > 0", name=op.f("ck_saved_reports_dataset_not_blank")),
    )
    op.create_index("ix_saved_reports_shop_archived", "saved_reports", ["shop_id", "is_archived"])

    with op.batch_alter_table("scheduled_reports") as batch:
        batch.add_column(sa.Column("params", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("export_format", sa.String(10), nullable=True))
        batch.add_column(sa.Column("delivery_channel", sa.String(20), nullable=True))
        batch.add_column(sa.Column("recipients", sa.JSON(), nullable=True))

    op.create_table(
        "report_runs",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("scheduled_report_id", ID, nullable=False),
        sa.Column("run_key", sa.String(60), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=False),
        sa.Column("period_end", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("delivery_status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_runs")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_report_runs_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id",
            "scheduled_report_id",
            "run_key",
            name=op.f("uq_report_runs_shop_id_scheduled_report_id_run_key"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_report_runs_shop_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "scheduled_report_id"],
            ["scheduled_reports.shop_id", "scheduled_reports.id"],
            name=op.f("fk_report_runs_shop_id_scheduled_report_id"),
        ),
    )
    op.create_index("ix_report_runs_shop_schedule", "report_runs", ["shop_id", "scheduled_report_id"])

    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                f"SELECT id, '{code}' FROM roles WHERE shop_id IS NULL AND code = '{role_code}'"
            )


def downgrade() -> None:
    op.drop_index("ix_report_runs_shop_schedule", table_name="report_runs")
    op.drop_table("report_runs")
    with op.batch_alter_table("scheduled_reports") as batch:
        batch.drop_column("recipients")
        batch.drop_column("delivery_channel")
        batch.drop_column("export_format")
        batch.drop_column("params")
    op.drop_index("ix_saved_reports_shop_archived", table_name="saved_reports")
    op.drop_table("saved_reports")
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "DELETE FROM role_permissions WHERE permission = "
                f"'{code}' AND role_id IN (SELECT id FROM roles WHERE shop_id IS NULL AND code = '{role_code}')"
            )
