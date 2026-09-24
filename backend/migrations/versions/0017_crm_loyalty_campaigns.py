"""Customer growth: CRM, loyalty, segmentation groups, campaigns, marketing automation and referrals (Phase 14)

* `customers` gains classification facts (`customer_type`, `source`, `tags`), marketing consent per channel
  (`marketing_opt_in_email/sms/whatsapp/push`, all defaulting to False — never opted in without asking),
  `preferred_contact_channel`, and `referred_by_customer_id`. No second customer table.
* CRM: insert-only `customer_notes` (a customer's timeline notes), `customer_groups` and
  `customer_group_members` (manual or rule-based targeting groups).
* Loyalty: one `loyalty_programs` row per shop (configurable earning/redemption rules, none hardcoded) and an
  insert-only `loyalty_ledger` (a balance is the sum of its rows, exactly like `customer_ledger`).
* Campaigns: `campaigns`, insert-only `campaign_audience_snapshots` (who was targeted, frozen at launch) and
  insert-only `campaign_sends` (the honest per-customer delivery outcome — `NOT_CONFIGURED` unless a real
  provider exists, since none does anywhere in this codebase).
* Automation: `automation_rules` and insert-only `automation_runs` (execution history for cooldown checks).
* Referrals: `referral_programs`, `referral_codes`, `referral_events` (rewards are granted through the
  existing loyalty ledger, never a separate wallet).

Self-contained: no imports from `app`.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
INSERT_ONLY = [
    "customer_notes",
    "loyalty_ledger",
    "campaign_audience_snapshots",
    "campaign_sends",
    "automation_runs",
]

NEW_PERMISSIONS = {
    "OWNER": [
        "CRM_VIEW", "CRM_MANAGE", "CRM_SEGMENT_MANAGE", "CAMPAIGN_VIEW", "CAMPAIGN_MANAGE", "CAMPAIGN_LAUNCH",
        "LOYALTY_VIEW", "LOYALTY_MANAGE", "REFERRAL_VIEW", "REFERRAL_MANAGE", "CRM_ANALYTICS_VIEW",
        "AUTOMATION_MANAGE",
    ],
    "MANAGER": [
        "CRM_VIEW", "CRM_MANAGE", "CRM_SEGMENT_MANAGE", "CAMPAIGN_VIEW", "CAMPAIGN_MANAGE", "CAMPAIGN_LAUNCH",
        "LOYALTY_VIEW", "LOYALTY_MANAGE", "REFERRAL_VIEW", "REFERRAL_MANAGE", "CRM_ANALYTICS_VIEW",
        "AUTOMATION_MANAGE",
    ],
    "CASHIER": ["CRM_VIEW", "LOYALTY_VIEW"],
    "SALES_STAFF": ["CRM_VIEW", "CRM_MANAGE", "LOYALTY_VIEW"],
    "ACCOUNTANT": ["CRM_VIEW", "LOYALTY_VIEW", "CRM_ANALYTICS_VIEW"],
}  # fmt: skip


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


def upgrade() -> None:
    with op.batch_alter_table("shops") as batch_op:
        batch_op.add_column(sa.Column("crm_campaign_audience_threshold", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("crm_loyalty_adjustment_threshold", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "crm_campaign_audience_threshold_non_negative",
            "crm_campaign_audience_threshold IS NULL OR crm_campaign_audience_threshold >= 0",
        )
        batch_op.create_check_constraint(
            "crm_loyalty_adjustment_threshold_non_negative",
            "crm_loyalty_adjustment_threshold IS NULL OR crm_loyalty_adjustment_threshold >= 0",
        )
    with op.batch_alter_table("customers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "customer_type",
                sa.Enum("RETAIL", "WHOLESALE", "OTHER", name="customer_type", native_enum=False, create_constraint=True, length=9),
                nullable=True,
            )
        )  # fmt: skip
        batch_op.add_column(
            sa.Column(
                "source",
                sa.Enum("WALK_IN", "REFERRAL", "ONLINE", "CAMPAIGN", "OTHER", name="customer_source", native_enum=False, create_constraint=True, length=8),
                nullable=True,
            )
        )  # fmt: skip
        batch_op.add_column(sa.Column("tags", sa.JSON(), server_default="[]", nullable=False))
        batch_op.add_column(
            sa.Column(
                "preferred_contact_channel",
                sa.Enum("IN_APP", "EMAIL", "SMS", "WHATSAPP", "PUSH", name="preferred_contact_channel", native_enum=False, create_constraint=True, length=8),
                nullable=True,
            )
        )  # fmt: skip
        batch_op.add_column(
            sa.Column("marketing_opt_in_email", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("marketing_opt_in_sms", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("marketing_opt_in_whatsapp", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("marketing_opt_in_push", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("referred_by_customer_id", ID, nullable=True))
    with op.batch_alter_table("customers") as batch_op:
        batch_op.create_foreign_key(
            op.f("fk_customers_shop_id_referred_by_customer_id"),
            "customers", ["shop_id", "referred_by_customer_id"], ["shop_id", "id"],
        )  # fmt: skip
    op.create_index("ix_customers_shop_id_referred_by", "customers", ["shop_id", "referred_by_customer_id"])

    # --- CRM: notes and groups ---------------------------------------------------------------------------

    op.create_table(
        "customer_notes",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_notes")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_customer_notes_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_customer_notes_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "user_id"], ["users.shop_id", "users.id"], name=op.f("fk_customer_notes_shop_id_user_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_customer_notes_shop_id")),
        sa.CheckConstraint("length(trim(body)) > 0", name=op.f("ck_customer_notes_body_not_blank")),
    )  # fmt: skip
    op.create_index("ix_customer_notes_shop_customer", "customer_notes", ["shop_id", "customer_id"])

    op.create_table(
        "customer_groups",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("MANUAL", "RULE_BASED", name="customer_group_kind", native_enum=False, create_constraint=True, length=10),
            nullable=False,
        ),
        sa.Column("rule", sa.JSON(), nullable=True),
        sa.Column("last_recalculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_groups")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_customer_groups_shop_id_id")),
        sa.UniqueConstraint("shop_id", "name", name=op.f("uq_customer_groups_shop_id_name")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_customer_groups_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_customer_groups_shop_id")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_customer_groups_name_not_blank")),
    )  # fmt: skip
    op.create_index("ix_customer_groups_shop_kind", "customer_groups", ["shop_id", "kind"])

    op.create_table(
        "customer_group_members",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("group_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_group_members")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_customer_group_members_shop_id_id")),
        sa.UniqueConstraint("shop_id", "group_id", "customer_id", name=op.f("uq_customer_group_members_shop_id_group_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "group_id"], ["customer_groups.shop_id", "customer_groups.id"], name=op.f("fk_customer_group_members_shop_id_group_id")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_customer_group_members_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_customer_group_members_shop_id")),
    )  # fmt: skip
    op.create_index("ix_customer_group_members_shop_group", "customer_group_members", ["shop_id", "group_id"])

    # --- Loyalty -------------------------------------------------------------------------------------------

    op.create_table(
        "loyalty_programs",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("points_per_amount", sa.BigInteger(), nullable=False),
        sa.Column("min_transaction_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("redemption_value", sa.BigInteger(), nullable=False),
        sa.Column("min_redemption_points", sa.Integer(), nullable=True),
        sa.Column("max_redeem_points_per_txn", sa.Integer(), nullable=True),
        sa.Column("points_expiry_days", sa.Integer(), nullable=True),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loyalty_programs")),
        sa.UniqueConstraint("shop_id", name=op.f("uq_loyalty_programs_shop_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_loyalty_programs_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_loyalty_programs_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_loyalty_programs_shop_id")),
        sa.CheckConstraint("points_per_amount > 0", name=op.f("ck_loyalty_programs_points_per_amount_positive")),
        sa.CheckConstraint("min_transaction_amount >= 0", name=op.f("ck_loyalty_programs_min_transaction_amount_non_negative")),
        sa.CheckConstraint("redemption_value > 0", name=op.f("ck_loyalty_programs_redemption_value_positive")),
        sa.CheckConstraint("min_redemption_points IS NULL OR min_redemption_points > 0", name=op.f("ck_loyalty_programs_min_redemption_points_positive")),
        sa.CheckConstraint("max_redeem_points_per_txn IS NULL OR max_redeem_points_per_txn > 0", name=op.f("ck_loyalty_programs_max_redeem_points_per_txn_positive")),
        sa.CheckConstraint("points_expiry_days IS NULL OR points_expiry_days > 0", name=op.f("ck_loyalty_programs_points_expiry_days_positive")),
    )  # fmt: skip

    op.create_table(
        "loyalty_ledger",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum("EARN", "REDEEM", "ADJUST", "EXPIRE", "REVERSAL", name="loyalty_entry_type", native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column("points_delta", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(20), nullable=False),
        sa.Column("reference_id", ID, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loyalty_ledger")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_loyalty_ledger_shop_id_id")),
        sa.UniqueConstraint("shop_id", "customer_id", "entry_type", "reference_type", "reference_id", name="uq_loyalty_ledger_no_double_entry"),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_loyalty_ledger_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_loyalty_ledger_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_loyalty_ledger_shop_id")),
    )  # fmt: skip
    op.create_index("ix_loyalty_ledger_shop_customer", "loyalty_ledger", ["shop_id", "customer_id"])

    # --- Campaigns -----------------------------------------------------------------------------------------

    op.create_table(
        "campaigns",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "SCHEDULED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED", name="campaign_status", native_enum=False, create_constraint=True, length=9),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("IN_APP", "EMAIL", "SMS", "WHATSAPP", "PUSH", name="channel", native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column("target_group_id", ID, nullable=True),
        sa.Column("target_segment", sa.String(40), nullable=True),
        sa.Column("promotion_id", ID, nullable=True),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_by", ID, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", ID, nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_campaigns_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "target_group_id"], ["customer_groups.shop_id", "customer_groups.id"], name=op.f("fk_campaigns_shop_id_target_group_id")),
        sa.ForeignKeyConstraint(["shop_id", "promotion_id"], ["promotions.shop_id", "promotions.id"], name=op.f("fk_campaigns_shop_id_promotion_id")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_campaigns_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id", "launched_by"], ["users.shop_id", "users.id"], name=op.f("fk_campaigns_shop_id_launched_by")),
        sa.ForeignKeyConstraint(["shop_id", "cancelled_by"], ["users.shop_id", "users.id"], name=op.f("fk_campaigns_shop_id_cancelled_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_campaigns_shop_id")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_campaigns_name_not_blank")),
    )  # fmt: skip
    op.create_index("ix_campaigns_shop_status", "campaigns", ["shop_id", "status"])

    op.create_table(
        "campaign_audience_snapshots",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("campaign_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_audience_snapshots")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_campaign_audience_snapshots_shop_id_id")),
        sa.UniqueConstraint("shop_id", "campaign_id", "customer_id", name=op.f("uq_campaign_audience_snapshots_shop_id_campaign_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "campaign_id"], ["campaigns.shop_id", "campaigns.id"], name=op.f("fk_campaign_audience_snapshots_shop_id_campaign_id")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_campaign_audience_snapshots_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_campaign_audience_snapshots_shop_id")),
    )  # fmt: skip
    op.create_index(
        "ix_campaign_audience_shop_campaign", "campaign_audience_snapshots", ["shop_id", "campaign_id"]
    )

    op.create_table(
        "campaign_sends",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("campaign_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column(
            "channel",
            sa.Enum("IN_APP", "EMAIL", "SMS", "WHATSAPP", "PUSH", name="channel", native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("SENT", "NOT_CONFIGURED", "SKIPPED_NO_CONSENT", "SKIPPED_OPTED_OUT", "FAILED", name="campaign_send_status", native_enum=False, create_constraint=True, length=18),
            nullable=False,
        ),
        sa.Column("detail", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_sends")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_campaign_sends_shop_id_id")),
        sa.UniqueConstraint("shop_id", "campaign_id", "customer_id", name=op.f("uq_campaign_sends_shop_id_campaign_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "campaign_id"], ["campaigns.shop_id", "campaigns.id"], name=op.f("fk_campaign_sends_shop_id_campaign_id")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_campaign_sends_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_campaign_sends_shop_id")),
    )  # fmt: skip
    op.create_index("ix_campaign_sends_shop_campaign", "campaign_sends", ["shop_id", "campaign_id"])

    # --- Automation ------------------------------------------------------------------------------------------

    op.create_table(
        "automation_rules",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "trigger_type",
            sa.Enum("NEW_CUSTOMER", "INACTIVITY", "LOYALTY_MILESTONE", "PURCHASE_MILESTONE", name="automation_trigger", native_enum=False, create_constraint=True, length=18),
            nullable=False,
        ),
        sa.Column("conditions", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "action_type",
            sa.Enum("CREATE_CAMPAIGN_DRAFT", "CREATE_TASK", "NOTIFY", name="automation_action", native_enum=False, create_constraint=True, length=21),
            nullable=False,
        ),
        sa.Column("action_config", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("cooldown_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_rules")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_automation_rules_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_automation_rules_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_automation_rules_shop_id")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_automation_rules_name_not_blank")),
        sa.CheckConstraint("cooldown_days >= 0", name=op.f("ck_automation_rules_cooldown_days_non_negative")),
    )  # fmt: skip
    op.create_index("ix_automation_rules_shop_active", "automation_rules", ["shop_id", "is_active"])

    op.create_table(
        "automation_runs",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("rule_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=True),
        sa.Column(
            "status",
            sa.Enum("SUCCESS", "SKIPPED_COOLDOWN", "SKIPPED_CONDITION", "FAILED", name="automation_run_status", native_enum=False, create_constraint=True, length=17),
            nullable=False,
        ),
        sa.Column("result", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_runs")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_automation_runs_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "rule_id"], ["automation_rules.shop_id", "automation_rules.id"], name=op.f("fk_automation_runs_shop_id_rule_id")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_automation_runs_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_automation_runs_shop_id")),
    )  # fmt: skip
    op.create_index(
        "ix_automation_runs_shop_rule_customer", "automation_runs", ["shop_id", "rule_id", "customer_id"]
    )

    # --- Referrals -------------------------------------------------------------------------------------------

    op.create_table(
        "referral_programs",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("referrer_reward_points", sa.Integer(), nullable=True),
        sa.Column("referred_reward_points", sa.Integer(), nullable=True),
        sa.Column("min_purchase_amount", sa.BigInteger(), nullable=True),
        sa.Column("max_referrals_per_customer", sa.Integer(), nullable=True),
        sa.Column("expiry_days", sa.Integer(), nullable=True),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referral_programs")),
        sa.UniqueConstraint("shop_id", name=op.f("uq_referral_programs_shop_id")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_referral_programs_shop_id_id")),
        sa.ForeignKeyConstraint(["shop_id", "created_by"], ["users.shop_id", "users.id"], name=op.f("fk_referral_programs_shop_id_created_by")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_referral_programs_shop_id")),
        sa.CheckConstraint("referrer_reward_points IS NULL OR referrer_reward_points > 0", name=op.f("ck_referral_programs_referrer_reward_points_positive")),
        sa.CheckConstraint("referred_reward_points IS NULL OR referred_reward_points > 0", name=op.f("ck_referral_programs_referred_reward_points_positive")),
        sa.CheckConstraint("min_purchase_amount IS NULL OR min_purchase_amount >= 0", name=op.f("ck_referral_programs_min_purchase_amount_non_negative")),
        sa.CheckConstraint("max_referrals_per_customer IS NULL OR max_referrals_per_customer > 0", name=op.f("ck_referral_programs_max_referrals_per_customer_positive")),
        sa.CheckConstraint("expiry_days IS NULL OR expiry_days > 0", name=op.f("ck_referral_programs_expiry_days_positive")),
    )  # fmt: skip

    op.create_table(
        "referral_codes",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("customer_id", ID, nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referral_codes")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_referral_codes_shop_id_id")),
        sa.UniqueConstraint("shop_id", "code", name=op.f("uq_referral_codes_shop_id_code")),
        sa.ForeignKeyConstraint(["shop_id", "customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_referral_codes_shop_id_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_referral_codes_shop_id")),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_referral_codes_code_not_blank")),
    )  # fmt: skip
    op.create_index("ix_referral_codes_shop_customer", "referral_codes", ["shop_id", "customer_id"])

    op.create_table(
        "referral_events",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("referral_code_id", ID, nullable=False),
        sa.Column("referrer_customer_id", ID, nullable=False),
        sa.Column("referred_customer_id", ID, nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "QUALIFIED", "REWARDED", "EXPIRED", "INVALID", name="referral_event_status", native_enum=False, create_constraint=True, length=9),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("qualifying_reference_type", sa.String(20), nullable=True),
        sa.Column("qualifying_reference_id", ID, nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referral_events")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_referral_events_shop_id_id")),
        sa.UniqueConstraint("shop_id", "referred_customer_id", name="uq_referral_events_referred_once"),
        sa.ForeignKeyConstraint(["shop_id", "referral_code_id"], ["referral_codes.shop_id", "referral_codes.id"], name=op.f("fk_referral_events_shop_id_referral_code_id")),
        sa.ForeignKeyConstraint(["shop_id", "referrer_customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_referral_events_shop_id_referrer_customer_id")),
        sa.ForeignKeyConstraint(["shop_id", "referred_customer_id"], ["customers.shop_id", "customers.id"], name=op.f("fk_referral_events_shop_id_referred_customer_id")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_referral_events_shop_id")),
    )  # fmt: skip
    op.create_index(
        "ix_referral_events_shop_referrer", "referral_events", ["shop_id", "referrer_customer_id"]
    )
    op.create_index("ix_referral_events_shop_status", "referral_events", ["shop_id", "status"])

    # --- Permissions and the AI action kind -------------------------------------------------------------------

    with op.batch_alter_table("ai_actions") as batch_op:
        batch_op.drop_constraint("kind", type_="check")
        batch_op.create_check_constraint(
            "kind",
            "kind IN ('PURCHASE_DRAFT', 'STOCK_ADJUSTMENT', 'PROMOTION_DRAFT', 'TASK_DRAFT', 'CAMPAIGN_DRAFT')",
        )

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
            "kind", "kind IN ('PURCHASE_DRAFT', 'STOCK_ADJUSTMENT', 'PROMOTION_DRAFT', 'TASK_DRAFT')"
        )
    for table in (
        "referral_events", "referral_codes", "referral_programs",
        "automation_runs", "automation_rules",
        "campaign_sends", "campaign_audience_snapshots", "campaigns",
        "loyalty_ledger", "loyalty_programs",
        "customer_group_members", "customer_groups", "customer_notes",
    ):  # fmt: skip
        op.drop_table(table)
    with op.batch_alter_table("shops") as batch_op:
        batch_op.drop_constraint("crm_campaign_audience_threshold_non_negative", type_="check")
        batch_op.drop_constraint("crm_loyalty_adjustment_threshold_non_negative", type_="check")
        batch_op.drop_column("crm_loyalty_adjustment_threshold")
        batch_op.drop_column("crm_campaign_audience_threshold")
    op.drop_index("ix_customers_shop_id_referred_by", table_name="customers")
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_constraint(op.f("fk_customers_shop_id_referred_by_customer_id"), type_="foreignkey")
        batch_op.drop_constraint("customer_type", type_="check")
        batch_op.drop_constraint("customer_source", type_="check")
        batch_op.drop_constraint("preferred_contact_channel", type_="check")
        batch_op.drop_column("referred_by_customer_id")
        batch_op.drop_column("marketing_opt_in_push")
        batch_op.drop_column("marketing_opt_in_whatsapp")
        batch_op.drop_column("marketing_opt_in_sms")
        batch_op.drop_column("marketing_opt_in_email")
        batch_op.drop_column("preferred_contact_channel")
        batch_op.drop_column("tags")
        batch_op.drop_column("source")
        batch_op.drop_column("customer_type")
