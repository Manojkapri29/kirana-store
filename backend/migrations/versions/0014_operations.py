"""SaaS operations, shop account lifecycle, notifications, request ids on the audit log

* Shops gain `account_status` (ACTIVE, TRIAL, SUSPENDED, DEACTIVATED), a reason and a time. Every existing shop is ACTIVE.
  Nothing is deleted or rewritten: this only records what each shop may do.
* `audit_log` gains a nullable `request_id` (added in place, not by rebuilding the table, so its insert-only triggers stay).
* System administration: `system_admins` (token hash only), `admin_audit_logs` (insert-only), `support_access_grants`,
  `system_events` (safe platform facts), `backup_records` and `restore_records`.
* Notifications: `notification_events` (one per occurrence, de-duplicated), `notification_deliveries` (one per person and
  channel, with a delivery state) and `notification_preferences`.
* Plan data: the feature `exports` is added to every plan as enabled, so nothing that worked changes.

Self-contained: no imports from `app`.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INSERT_ONLY = ["admin_audit_logs"]
STATES = ("ACTIVE", "TRIAL", "SUSPENDED", "DEACTIVATED")


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
    op.create_table(
        "backup_records",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("backup_key", sa.String(length=60), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "MANUAL",
                "SCHEDULED",
                "PRE_RESTORE",
                name="backup_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "VERIFIED",
                "FAILED",
                "DELETED",
                name="backup_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("storage_provider", sa.String(length=30), nullable=False),
        sa.Column("filename", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("schema_revision", sa.String(length=30), nullable=True),
        sa.Column("initiated_by", sa.String(length=120), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(backup_key)) > 0", name=op.f("ck_backup_records_backup_key_not_blank")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_backup_records_size_bytes_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_records")),
        sa.UniqueConstraint("backup_key", name=op.f("uq_backup_records_backup_key")),
    )
    op.create_table(
        "restore_records",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("backup_key", sa.String(length=60), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "VALIDATE",
                "REHEARSAL",
                "RESTORE",
                name="restore_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("initiated_by", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restore_records")),
    )
    op.create_table(
        "system_admins",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "SUPER_ADMIN",
                "SUPPORT_ADMIN",
                "OPERATIONS_ADMIN",
                name="admin_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=12), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0", name=op.f("ck_system_admins_display_name_not_blank")
        ),
        sa.CheckConstraint("length(trim(email)) > 0", name=op.f("ck_system_admins_email_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_admins")),
        sa.UniqueConstraint("email", name=op.f("uq_system_admins_email")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_system_admins_token_hash")),
    )
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("admin_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("permission", sa.String(length=40), nullable=True),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("target_shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(action)) > 0", name=op.f("ck_admin_audit_logs_action_not_blank")),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["system_admins.id"], name=op.f("fk_admin_audit_logs_admin_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_logs")),
    )
    with op.batch_alter_table("admin_audit_logs", schema=None) as batch_op:
        batch_op.create_index("ix_admin_audit_admin", ["admin_id", "created_at"], unique=False)
        batch_op.create_index("ix_admin_audit_created", ["created_at"], unique=False)

    op.create_table(
        "notification_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0", name=op.f("ck_notification_events_event_type_not_blank")
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_notification_events_title_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_notification_events_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_events")),
        sa.UniqueConstraint("shop_id", "dedupe_key", name=op.f("uq_notification_events_shop_id_dedupe_key")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_notification_events_shop_id_id")),
    )
    with op.batch_alter_table("notification_events", schema=None) as batch_op:
        batch_op.create_index("ix_notification_events_shop_created", ["shop_id", "created_at"], unique=False)

    op.create_table(
        "support_access_grants",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("admin_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("granted_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(reason)) > 0", name=op.f("ck_support_access_grants_reason_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["system_admins.id"], name=op.f("fk_support_access_grants_admin_id")
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["system_admins.id"], name=op.f("fk_support_access_grants_granted_by")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_support_access_grants_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_access_grants")),
    )
    with op.batch_alter_table("support_access_grants", schema=None) as batch_op:
        batch_op.create_index("ix_support_grants_shop_admin", ["shop_id", "admin_id"], unique=False)

    op.create_table(
        "system_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "INFO", "WARNING", "ERROR", name="event_severity", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(category)) > 0", name=op.f("ck_system_events_category_not_blank")),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_system_events_code_not_blank")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_system_events_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_events")),
    )
    with op.batch_alter_table("system_events", schema=None) as batch_op:
        batch_op.create_index("ix_system_events_category_created", ["category", "created_at"], unique=False)
        batch_op.create_index("ix_system_events_shop_created", ["shop_id", "created_at"], unique=False)

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("event_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "IN_APP",
                "EMAIL",
                "SMS",
                "WHATSAPP",
                "PUSH",
                name="notification_channel",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SENT",
                "FAILED",
                "RETRYING",
                "CANCELLED",
                name="delivery_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_notification_deliveries_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id", "event_id"],
            ["notification_events.shop_id", "notification_events.id"],
            name=op.f("fk_notification_deliveries_shop_id_event_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_notification_deliveries_shop_id_user_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_notification_deliveries_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint(
            "event_id", "user_id", "channel", name=op.f("uq_notification_deliveries_event_id_user_id_channel")
        ),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_notification_deliveries_shop_id_id")),
    )
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.create_index("ix_notification_deliveries_due", ["status", "next_attempt_at"], unique=False)
        batch_op.create_index(
            "ix_notification_deliveries_inbox", ["shop_id", "user_id", "channel", "read_at"], unique=False
        )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.Column("sms", sa.Boolean(), nullable=False),
        sa.Column("whatsapp", sa.Boolean(), nullable=False),
        sa.Column("push", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "user_id"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_notification_preferences_shop_id_user_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_notification_preferences_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_preferences")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_notification_preferences_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id",
            "user_id",
            "category",
            name=op.f("uq_notification_preferences_shop_id_user_id_category"),
        ),
    )
    # In place (not a batch rebuild): audit_log has insert-only triggers that a table rebuild would drop.
    op.add_column("audit_log", sa.Column("request_id", sa.String(length=64), nullable=True))

    with op.batch_alter_table("shops", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "account_status",
                sa.Enum(
                    "ACTIVE",
                    "TRIAL",
                    "SUSPENDED",
                    "DEACTIVATED",
                    name="account_status",
                    native_enum=False,
                    create_constraint=True,
                ),
                server_default="ACTIVE",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("status_reason", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    _create_triggers()
    for key in ("max_exports_per_month", "max_image_analyses_per_month"):  # unlimited (no cap) on every plan
        op.execute(
            sa.text(
                "INSERT INTO plan_features (plan_id, feature_key, enabled, limit_value) SELECT id, :key, :on, NULL FROM plans "
                "WHERE NOT EXISTS (SELECT 1 FROM plan_features f WHERE f.plan_id = plans.id AND f.feature_key = :key)"
            ).bindparams(key=key, on=True)
        )
    op.execute(
        sa.text(
            "INSERT INTO plan_features (plan_id, feature_key, enabled) SELECT id, 'exports', :on FROM plans "
            "WHERE NOT EXISTS (SELECT 1 FROM plan_features f WHERE f.plan_id = plans.id AND f.feature_key = 'exports')"
        ).bindparams(on=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("shops", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_shops_account_status"), type_="check")
        batch_op.drop_column("status_changed_at")
        batch_op.drop_column("status_reason")
        batch_op.drop_column("account_status")

    op.execute(
        sa.text(
            "DELETE FROM plan_features WHERE feature_key IN ('exports', 'max_exports_per_month', 'max_image_analyses_per_month')"
        )
    )
    _drop_triggers()
    op.drop_column("audit_log", "request_id")

    op.drop_table("notification_preferences")
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_deliveries_inbox")
        batch_op.drop_index("ix_notification_deliveries_due")

    op.drop_table("notification_deliveries")
    with op.batch_alter_table("system_events", schema=None) as batch_op:
        batch_op.drop_index("ix_system_events_shop_created")
        batch_op.drop_index("ix_system_events_category_created")

    op.drop_table("system_events")
    with op.batch_alter_table("support_access_grants", schema=None) as batch_op:
        batch_op.drop_index("ix_support_grants_shop_admin")

    op.drop_table("support_access_grants")
    with op.batch_alter_table("notification_events", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_events_shop_created")

    op.drop_table("notification_events")
    with op.batch_alter_table("admin_audit_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_admin_audit_created")
        batch_op.drop_index("ix_admin_audit_admin")

    op.drop_table("admin_audit_logs")
    op.drop_table("system_admins")
    op.drop_table("restore_records")
    op.drop_table("backup_records")
