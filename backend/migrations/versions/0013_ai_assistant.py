"""AI assistant: usage tracking, confirmed-action records, and the plan entitlements for AI

* `ai_usage`: one row per request that reached the AI layer (feature, provider, model, status, token counts when the
  provider gave them). No question, prompt, answer or document text is stored.
* `ai_actions`: a change the AI prepared (a purchase draft, a stock adjustment, an offer draft) and what a person
  did with it. `proposal` never changes; `current` is what a confirmation would do. Confirming runs an existing
  service; nothing here writes business data by itself.
* Plan data: three features (`ai_assistant`, `ai_insights`, `ai_documents`) and a monthly limit
  (`max_ai_requests_per_month`). Example values only, editable like every plan entry: free gets basic questions, basic
  adds insights, pro adds document intelligence. No commercial pricing is implied.

Self-contained: no imports from `app`.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

# (plan code, feature key, enabled, limit_value)
PLAN_ROWS = [
    ("free", "ai_assistant", True, None),
    ("free", "ai_insights", False, None),
    ("free", "ai_documents", False, None),
    ("free", "max_ai_requests_per_month", True, 30),
    ("basic", "ai_assistant", True, None),
    ("basic", "ai_insights", True, None),
    ("basic", "ai_documents", False, None),
    ("basic", "max_ai_requests_per_month", True, 300),
    ("pro", "ai_assistant", True, None),
    ("pro", "ai_insights", True, None),
    ("pro", "ai_documents", True, None),
    ("pro", "max_ai_requests_per_month", True, None),
]


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(feature)) > 0", name=op.f("ck_ai_usage_feature_not_blank")),
        sa.CheckConstraint("input_tokens >= 0", name=op.f("ck_ai_usage_input_tokens_non_negative")),
        sa.CheckConstraint("output_tokens >= 0", name=op.f("ck_ai_usage_output_tokens_non_negative")),
        sa.CheckConstraint(
            "estimated_cost_micros >= 0", name=op.f("ck_ai_usage_estimated_cost_micros_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"], ["users.shop_id", "users.id"], name=op.f("fk_ai_usage_shop_id_user_id")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_ai_usage_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_ai_usage_shop_id_id")),
    )
    op.create_index("ix_ai_usage_shop_created", "ai_usage", ["shop_id", "created_at"])

    op.create_table(
        "ai_actions",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "PURCHASE_DRAFT",
                "STOCK_ADJUSTMENT",
                "PROMOTION_DRAFT",
                name="kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PROPOSED",
                "EXECUTED",
                "CANCELLED",
                "FAILED",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result_type", sa.String(length=30), nullable=True),
        sa.Column("result_ids", sa.JSON(), nullable=True),
        sa.Column("failure_message", sa.String(length=300), nullable=True),
        sa.Column("reference_id", sa.String(length=30), nullable=True),
        sa.Column("decided_by", ID, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_ai_actions_attempts_non_negative")),
        sa.CheckConstraint("length(trim(feature)) > 0", name=op.f("ck_ai_actions_feature_not_blank")),
        sa.CheckConstraint(
            "status = 'EXECUTED' OR result_type IS NULL", name=op.f("ck_ai_actions_result_only_when_executed")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_ai_actions_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_ai_actions_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_actions")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_ai_actions_shop_id_id")),
    )
    op.create_index("ix_ai_actions_shop_status", "ai_actions", ["shop_id", "status"])

    for plan_code, key, enabled, limit in PLAN_ROWS:
        op.execute(
            sa.text(
                "INSERT INTO plan_features (plan_id, feature_key, enabled, limit_value) "
                "SELECT id, :key, :enabled, :limit FROM plans WHERE code = :code "
                "AND NOT EXISTS (SELECT 1 FROM plan_features f WHERE f.plan_id = plans.id "
                "AND f.feature_key = :key)"
            ).bindparams(key=key, enabled=enabled, limit=limit, code=plan_code)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM plan_features WHERE feature_key IN "
            "('ai_assistant', 'ai_insights', 'ai_documents', 'max_ai_requests_per_month')"
        )
    )
    op.drop_index("ix_ai_actions_shop_status", table_name="ai_actions")
    op.drop_table("ai_actions")
    op.drop_index("ix_ai_usage_shop_created", table_name="ai_usage")
    op.drop_table("ai_usage")
