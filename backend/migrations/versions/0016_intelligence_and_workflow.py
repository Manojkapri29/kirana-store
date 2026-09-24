"""Advanced operations, business intelligence and workflow automation (Phase 13)

* `products` gains `pack_size` and `moq` (both optional, NULL = not set); `suppliers` gains `lead_time_days` (optional).
  Reorder planning uses them when present and says explicitly when it is assuming 1 unit / no minimum / no lead time.
* `shops` gains `stock_count_variance_threshold` (optional money): a posted stock count whose variance is worth at least
  this much needs a separate approval. NULL means no extra approval is configured.
* Stock counting: `stock_counts` (the count's scope and lifecycle) and `stock_count_items` (one product's expected and
  counted quantity). The only lasting effect of a posted count is ordinary `ADJUSTMENT` rows in `inventory_transactions`,
  written by `inventory_service` exactly as any other adjustment.
* Workflow automation: `business_tasks` and insert-only `task_comments`.
* A generic approval queue: `approval_requests` (used today only for large stock-count variances).
* Scheduled reports: `scheduled_reports` (no email is sent; a run's small summary is stored and an in-app notification
  points at it).

Self-contained: no imports from `app`.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
INSERT_ONLY = ["task_comments"]


def _create_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in INSERT_ONLY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is insert-only'); END"
                )
    elif dialect == "postgresql":
        for table in INSERT_ONLY:
            op.execute(
                f"CREATE TRIGGER trg_{table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION kirana_forbid_change()"
            )


def _drop_triggers() -> None:
    dialect = op.get_bind().dialect.name
    for table in INSERT_ONLY:
        if dialect == "sqlite":
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
        elif dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_insert_only ON {table}")


NEW_PERMISSIONS = {
    "OWNER": ["STOCK_COUNT_VIEW", "STOCK_COUNT_CREATE", "STOCK_COUNT_REVIEW", "STOCK_COUNT_APPROVE", "STOCK_COUNT_POST", "TASK_VIEW", "TASK_CREATE", "TASK_ASSIGN", "TASK_COMPLETE", "TASK_CANCEL", "SCHEDULED_REPORT_MANAGE"],
    "MANAGER": ["STOCK_COUNT_VIEW", "STOCK_COUNT_CREATE", "STOCK_COUNT_REVIEW", "STOCK_COUNT_APPROVE", "STOCK_COUNT_POST", "TASK_VIEW", "TASK_CREATE", "TASK_ASSIGN", "TASK_COMPLETE", "TASK_CANCEL", "SCHEDULED_REPORT_MANAGE"],
    "CASHIER": ["TASK_VIEW", "TASK_CREATE", "TASK_COMPLETE"],
    "INVENTORY_STAFF": ["STOCK_COUNT_VIEW", "STOCK_COUNT_CREATE", "STOCK_COUNT_REVIEW", "TASK_VIEW", "TASK_CREATE", "TASK_COMPLETE"],
    "SALES_STAFF": ["TASK_VIEW", "TASK_CREATE", "TASK_COMPLETE"],
    "ACCOUNTANT": ["TASK_VIEW", "TASK_CREATE", "TASK_COMPLETE"],
}  # fmt: skip


def upgrade() -> None:
    # The AI can now also draft a task (task_service.create): still nothing financial or inventory-changing.
    with op.batch_alter_table("ai_actions") as batch_op:
        batch_op.drop_constraint("kind", type_="check")
        batch_op.create_check_constraint(
            "kind", "kind IN ('PURCHASE_DRAFT', 'STOCK_ADJUSTMENT', 'PROMOTION_DRAFT', 'TASK_DRAFT')"
        )

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("pack_size", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("moq", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint("pack_size_non_negative", "pack_size >= 0")
        batch_op.create_check_constraint("moq_non_negative", "moq >= 0")
    with op.batch_alter_table("suppliers") as batch_op:
        batch_op.add_column(sa.Column("lead_time_days", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "lead_time_days_positive", "lead_time_days IS NULL OR lead_time_days > 0"
        )
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(sa.Column("stock_count_variance_threshold", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            "stock_count_variance_threshold_non_negative", "stock_count_variance_threshold >= 0"
        )

    op.create_table(
        "stock_counts",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "FULL",
                "CATEGORY",
                "PRODUCTS",
                name="stock_count_scope",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("category_id", ID, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "COUNTING",
                "REVIEW",
                "APPROVED",
                "POSTED",
                "CANCELLED",
                name="stock_count_status",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),  # fmt: skip
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("reviewed_by", ID, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", ID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by", ID, nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", ID, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_counts")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_stock_counts_shop_id_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "category_id"],
            ["categories.shop_id", "categories.id"],
            name=op.f("fk_stock_counts_shop_id_category_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_stock_counts_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "reviewed_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_stock_counts_shop_id_reviewed_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "approved_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_stock_counts_shop_id_approved_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "posted_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_stock_counts_shop_id_posted_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_stock_counts_shop_id")),
    )
    op.create_index("ix_stock_counts_shop_status", "stock_counts", ["shop_id", "status"])

    op.create_table(
        "stock_count_items",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("stock_count_id", ID, nullable=False),
        sa.Column("product_id", ID, nullable=False),
        sa.Column("expected_quantity", sa.BigInteger(), nullable=False),
        sa.Column("counted_quantity", sa.BigInteger(), nullable=True),
        sa.Column("variance", sa.BigInteger(), nullable=True),
        sa.Column("unit_cost_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("counted_by", ID, nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_count_items")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_stock_count_items_shop_id_id")),
        sa.UniqueConstraint(
            "stock_count_id", "product_id", name=op.f("uq_stock_count_items_stock_count_id_product_id")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "stock_count_id"],
            ["stock_counts.shop_id", "stock_counts.id"],
            name=op.f("fk_stock_count_items_shop_id_stock_count_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "product_id"],
            ["products.shop_id", "products.id"],
            name=op.f("fk_stock_count_items_shop_id_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "counted_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_stock_count_items_shop_id_counted_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_stock_count_items_shop_id")),
    )
    op.create_index("ix_stock_count_items_shop_count", "stock_count_items", ["shop_id", "stock_count_id"])

    op.create_table(
        "business_tasks",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(40), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "IN_PROGRESS",
                "WAITING",
                "COMPLETED",
                "CANCELLED",
                name="task_status",
                native_enum=False,
                create_constraint=True,
                length=11,
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                name="task_priority",
                native_enum=False,
                create_constraint=True,
                length=6,
            ),
            server_default="MEDIUM",
            nullable=False,
        ),
        sa.Column("assigned_to", ID, nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("entity_type", sa.String(40), nullable=True),
        sa.Column("entity_id", ID, nullable=True),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("completed_by", ID, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", ID, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_tasks")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_business_tasks_shop_id_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "assigned_to"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_business_tasks_shop_id_assigned_to"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_business_tasks_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "completed_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_business_tasks_shop_id_completed_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "cancelled_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_business_tasks_shop_id_cancelled_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_business_tasks_shop_id")),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_business_tasks_title_not_blank")),
    )
    op.create_index("ix_business_tasks_shop_status", "business_tasks", ["shop_id", "status"])
    op.create_index("ix_business_tasks_shop_assigned", "business_tasks", ["shop_id", "assigned_to", "status"])
    op.create_index(
        "ix_business_tasks_shop_entity", "business_tasks", ["shop_id", "entity_type", "entity_id"]
    )

    op.create_table(
        "task_comments",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("task_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_comments")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_task_comments_shop_id_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "task_id"],
            ["business_tasks.shop_id", "business_tasks.id"],
            name=op.f("fk_task_comments_shop_id_task_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_task_comments_shop_id_user_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_task_comments_shop_id")),
        sa.CheckConstraint("length(trim(body)) > 0", name=op.f("ck_task_comments_body_not_blank")),
    )
    op.create_index("ix_task_comments_shop_task", "task_comments", ["shop_id", "task_id"])

    op.create_table(
        "approval_requests",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", ID, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "CANCELLED",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("threshold_value", sa.BigInteger(), nullable=True),
        sa.Column("observed_value", sa.BigInteger(), nullable=True),
        sa.Column("requested_by", ID, nullable=False),
        sa.Column("decided_by", ID, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_approval_requests_shop_id_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "requested_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_approval_requests_shop_id_requested_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "decided_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_approval_requests_shop_id_decided_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_approval_requests_shop_id")),
        sa.CheckConstraint("length(trim(kind)) > 0", name=op.f("ck_approval_requests_kind_not_blank")),
        sa.CheckConstraint(
            "length(trim(entity_type)) > 0", name=op.f("ck_approval_requests_entity_type_not_blank")
        ),
    )
    op.create_index("ix_approval_requests_shop_status", "approval_requests", ["shop_id", "status"])
    op.create_index(
        "ix_approval_requests_shop_entity", "approval_requests", ["shop_id", "entity_type", "entity_id"]
    )

    op.create_table(
        "scheduled_reports",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column(
            "schedule",
            sa.Enum(
                "DAILY",
                "WEEKLY",
                "MONTHLY",
                name="report_schedule",
                native_enum=False,
                create_constraint=True,
                length=7,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_reports")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_scheduled_reports_shop_id_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_scheduled_reports_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_scheduled_reports_shop_id")),
        sa.CheckConstraint(
            "length(trim(report_type)) > 0", name=op.f("ck_scheduled_reports_report_type_not_blank")
        ),
    )
    op.create_index("ix_scheduled_reports_shop_active", "scheduled_reports", ["shop_id", "is_active"])

    # New permission codes for the system roles that gained access to stock counting, tasks and scheduled reports.
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                f"SELECT id, '{code}' FROM roles WHERE shop_id IS NULL AND code = '{role_code}'"
            )

    _create_triggers()


def downgrade() -> None:
    _drop_triggers()
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "DELETE FROM role_permissions WHERE permission = "
                f"'{code}' AND role_id IN (SELECT id FROM roles WHERE shop_id IS NULL AND code = '{role_code}')"
            )
    with op.batch_alter_table("ai_actions") as batch_op:
        batch_op.drop_constraint("kind", type_="check")
        batch_op.create_check_constraint(
            "kind", "kind IN ('PURCHASE_DRAFT', 'STOCK_ADJUSTMENT', 'PROMOTION_DRAFT')"
        )
    for table in (
        "scheduled_reports",
        "approval_requests",
        "task_comments",
        "business_tasks",
        "stock_count_items",
        "stock_counts",
    ):
        op.drop_table(table)
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_constraint("stock_count_variance_threshold_non_negative", type_="check")
        batch_op.drop_column("stock_count_variance_threshold")
    with op.batch_alter_table("suppliers") as batch_op:
        batch_op.drop_constraint("lead_time_days_positive", type_="check")
        batch_op.drop_column("lead_time_days")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("moq_non_negative", type_="check")
        batch_op.drop_constraint("pack_size_non_negative", type_="check")
        batch_op.drop_column("moq")
        batch_op.drop_column("pack_size")
