"""subscriptions: plans, plan features, shop subscriptions and usage counters

Adds the plan architecture (no payment processing) and seeds three EXAMPLE plans as editable data:
Free, Basic and Pro. Their prices are not published (Free is 0, the others NULL = "contact us"); features and
limits are placeholders that an operator changes with `python -m app.subscription_admin`. A shop with no
current subscription is treated as being on the Free plan, so nothing existing stops working.

Self-contained: plain SQLAlchemy only, no imports from `app`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

# (code, name, description, price, sort_order, features, limits). A limit of None means unlimited.
PLANS = [
    (
        "free", "Free", "The basics: products, stock and simple billing.", 0, 1,
        {"barcode_lookup": False, "promotions": False, "price_intelligence": False, "advanced_reports": False, "online_store": False},
        {"max_products": 100, "max_users": 2, "max_monthly_invoices": 100, "max_price_lookups_per_month": 0},
    ),
    (
        "basic", "Basic", "More room, barcode lookup, offers and fuller reports.", None, 2,
        {"barcode_lookup": True, "promotions": True, "price_intelligence": False, "advanced_reports": True, "online_store": False},
        {"max_products": 1000, "max_users": 5, "max_monthly_invoices": 2000, "max_price_lookups_per_month": 0},
    ),
    (
        "pro", "Pro", "Everything, including market price checks.", None, 3,
        {"barcode_lookup": True, "promotions": True, "price_intelligence": True, "advanced_reports": True, "online_store": True},
        {"max_products": None, "max_users": None, "max_monthly_invoices": None, "max_price_lookups_per_month": 300},
    ),
]  # fmt: skip


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", ID, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("billing_interval", sa.String(length=7), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_plans_code_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_plans_name_not_blank")),
        sa.CheckConstraint("price >= 0", name=op.f("ck_plans_price_non_negative")),
        sa.CheckConstraint(
            "billing_interval IN ('MONTHLY', 'YEARLY')", name=op.f("ck_plans_billing_interval")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("code", name=op.f("uq_plans_code")),
    )
    op.create_table(
        "plan_features",
        sa.Column("id", ID, nullable=False),
        sa.Column("plan_id", ID, nullable=False),
        sa.Column("feature_key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "length(trim(feature_key)) > 0", name=op.f("ck_plan_features_feature_key_not_blank")
        ),
        sa.CheckConstraint("limit_value >= 0", name=op.f("ck_plan_features_limit_value_non_negative")),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name=op.f("fk_plan_features_plan_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_features")),
        sa.UniqueConstraint("plan_id", "feature_key", name=op.f("uq_plan_features_plan_id_feature_key")),
    )
    op.create_table(
        "shop_subscriptions",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("plan_id", ID, nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('TRIAL', 'ACTIVE', 'CANCELLED', 'EXPIRED')", name=op.f("ck_shop_subscriptions_status")
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name=op.f("fk_shop_subscriptions_plan_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_shop_subscriptions_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_subscriptions")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_shop_subscriptions_shop_id_id")),
    )
    op.create_index(
        "uq_shop_subscriptions_current",
        "shop_subscriptions",
        ["shop_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('TRIAL', 'ACTIVE')"),
        postgresql_where=sa.text("status IN ('TRIAL', 'ACTIVE')"),
    )
    op.create_table(
        "subscription_usage",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("count >= 0", name=op.f("ck_subscription_usage_count_non_negative")),
        sa.CheckConstraint("length(trim(metric)) > 0", name=op.f("ck_subscription_usage_metric_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_subscription_usage_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_usage")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_subscription_usage_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "period", "metric", name=op.f("uq_subscription_usage_shop_id_period_metric")
        ),
    )

    now = datetime.now(UTC)
    plans = sa.table(
        "plans",
        sa.column("id", sa.Integer), sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.Text), sa.column("price", sa.BigInteger), sa.column("currency", sa.String),
        sa.column("billing_interval", sa.String), sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.BigInteger), sa.column("updated_at", sa.DateTime), sa.column("created_at", sa.DateTime),
    )  # fmt: skip
    features = sa.table(
        "plan_features",
        sa.column("plan_id", sa.Integer), sa.column("feature_key", sa.String),
        sa.column("enabled", sa.Boolean), sa.column("limit_value", sa.BigInteger),
    )  # fmt: skip
    for plan_id, (code, name, description, price, order, flags, limits) in enumerate(PLANS, start=1):
        op.bulk_insert(
            plans,
            [
                {
                    "id": plan_id, "code": code, "name": name, "description": description, "price": price,
                    "currency": "INR", "billing_interval": "MONTHLY", "is_active": True, "sort_order": order,
                    "updated_at": now, "created_at": now,
                }
            ],
        )  # fmt: skip
        rows = [
            {"plan_id": plan_id, "feature_key": key, "enabled": on, "limit_value": None}
            for key, on in flags.items()
        ]
        rows += [
            {"plan_id": plan_id, "feature_key": key, "enabled": True, "limit_value": value}
            for key, value in limits.items()
        ]
        op.bulk_insert(features, rows)


def downgrade() -> None:
    op.drop_table("subscription_usage")
    op.drop_index("uq_shop_subscriptions_current", table_name="shop_subscriptions")
    op.drop_table("shop_subscriptions")
    op.drop_table("plan_features")
    op.drop_table("plans")
