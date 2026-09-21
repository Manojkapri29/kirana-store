"""Sign-in accounts, shop memberships, roles and permissions, invitations, sessions, background jobs

* `accounts`: one sign-in identity per email. Every existing `users` row gets an account (same email and password hash;
  a password hash of "!" means "cannot sign in until a password is set", which is how the development owner starts).
* `users` becomes the shop MEMBERSHIP: it gains `account_id`, `role_id`, `status`, `invited_by`, `joined_at`, `removed_at` and
  `last_active_at`. Its email is no longer unique across shops (it is unique within a shop), so one person can belong to
  several shops. No row is deleted or rewritten: OWNER users get the OWNER role, STAFF users the CASHIER role.
* `roles` (system roles are global rows, custom roles belong to a shop) and `role_permissions`, seeded with the defaults.
* `invitations` (token hash only), `auth_sessions` (token hash and CSRF hash only).
* `background_jobs`: the database-backed job queue.

Self-contained: no imports from `app`. The permission defaults below are a frozen copy of `app/core/permissions.py`
as of this migration; later changes to a role are data changes, not migrations.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

ALL = [
    "PRODUCT_VIEW", "PRODUCT_CREATE", "PRODUCT_EDIT", "PRODUCT_DEACTIVATE", "INVENTORY_VIEW", "INVENTORY_ADJUST",
    "INVENTORY_EXPORT", "SUPPLIER_VIEW", "SUPPLIER_CREATE", "SUPPLIER_EDIT", "PURCHASE_VIEW", "PURCHASE_CREATE",
    "PURCHASE_POST", "PURCHASE_VOID", "SALE_VIEW", "SALE_CREATE", "SALE_POST", "SALE_VOID", "QUICK_SALE_CREATE",
    "RETURN_CREATE", "RETURN_POST", "CUSTOMER_VIEW", "CUSTOMER_CREATE", "CUSTOMER_EDIT", "KHATA_VIEW", "KHATA_PAYMENT",
    "KHATA_ADJUST", "PROMOTION_VIEW", "PROMOTION_CREATE", "PROMOTION_EDIT", "ONLINE_ORDER_VIEW", "ONLINE_ORDER_ACCEPT",
    "ONLINE_ORDER_REJECT", "ONLINE_ORDER_STATUS_UPDATE", "REPORT_VIEW", "REPORT_EXPORT", "AI_USE", "AI_ACTION_CONFIRM",
    "PRICE_INTELLIGENCE_USE", "STORE_SETTINGS_MANAGE", "STAFF_VIEW", "STAFF_INVITE", "STAFF_EDIT", "STAFF_SUSPEND",
    "ROLE_MANAGE", "SUBSCRIPTION_VIEW", "BACKUP_VIEW", "BACKUP_CREATE", "AUDIT_LOG_VIEW", "BUSINESS_SETTINGS_MANAGE",
]  # fmt: skip
OWNER_ONLY = {"ROLE_MANAGE", "BACKUP_CREATE"}

ROLES = {
    "OWNER": ("Owner", "Runs the shop. Holds every permission.", ALL),
    "MANAGER": ("Manager", "Runs the shop day to day. Everything except the owner-only actions.", [p for p in ALL if p not in OWNER_ONLY]),
    "CASHIER": (
        "Cashier", "Bills customers, records quick sales and payments.",
        ["PRODUCT_VIEW", "INVENTORY_VIEW", "SALE_VIEW", "SALE_CREATE", "SALE_POST", "QUICK_SALE_CREATE", "CUSTOMER_VIEW",
         "CUSTOMER_CREATE", "KHATA_VIEW", "KHATA_PAYMENT", "PROMOTION_VIEW"],
    ),
    "INVENTORY_STAFF": (
        "Inventory staff", "Looks after products and stock. No financial reports.",
        ["PRODUCT_VIEW", "PRODUCT_CREATE", "PRODUCT_EDIT", "INVENTORY_VIEW", "INVENTORY_ADJUST", "INVENTORY_EXPORT", "SUPPLIER_VIEW"],
    ),
    "SALES_STAFF": (
        "Sales staff", "Sells and looks after customers and simple order handling.",
        ["PRODUCT_VIEW", "INVENTORY_VIEW", "SALE_VIEW", "SALE_CREATE", "SALE_POST", "QUICK_SALE_CREATE", "CUSTOMER_VIEW",
         "CUSTOMER_CREATE", "CUSTOMER_EDIT", "KHATA_VIEW", "PROMOTION_VIEW", "ONLINE_ORDER_VIEW", "ONLINE_ORDER_ACCEPT",
         "ONLINE_ORDER_REJECT", "ONLINE_ORDER_STATUS_UPDATE"],
    ),
    "ACCOUNTANT": (
        "Accountant", "Purchases, khata, financial reports and exports. No stock adjustments.",
        ["PRODUCT_VIEW", "INVENTORY_VIEW", "SUPPLIER_VIEW", "PURCHASE_VIEW", "PURCHASE_CREATE", "PURCHASE_POST", "SALE_VIEW",
         "CUSTOMER_VIEW", "KHATA_VIEW", "KHATA_PAYMENT", "KHATA_ADJUST", "REPORT_VIEW", "REPORT_EXPORT", "AUDIT_LOG_VIEW"],
    ),
}  # fmt: skip


TRUE = "true"  # a literal both SQLite (3.23+) and PostgreSQL accept for a boolean column


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", ID, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE", "DISABLED", name="login_status", native_enum=False, create_constraint=True, length=8
            ),
            nullable=False,
        ),
        sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("email", name=op.f("uq_accounts_email")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_accounts_email_lowercase")),
        sa.CheckConstraint("length(trim(email)) > 0", name=op.f("ck_accounts_email_not_blank")),
        sa.CheckConstraint("length(trim(full_name)) > 0", name=op.f("ck_accounts_full_name_not_blank")),
        sa.CheckConstraint("failed_logins >= 0", name=op.f("ck_accounts_failed_logins_non_negative")),
    )
    op.create_table(
        "roles",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_roles_shop_id")),
        sa.UniqueConstraint("shop_id", "code", name=op.f("uq_roles_shop_id_code")),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_roles_code_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_roles_name_not_blank")),
        sa.CheckConstraint(
            "(is_system = 1 AND shop_id IS NULL) OR (is_system = 0 AND shop_id IS NOT NULL)",
            name=op.f("ck_roles_system_means_global"),
        ),
    )
    op.create_index(
        "uq_roles_system_code",
        "roles",
        ["code"],
        unique=True,
        sqlite_where=sa.text("shop_id IS NULL"),
        postgresql_where=sa.text("shop_id IS NULL"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", ID, nullable=False),
        sa.Column("permission", sa.String(60), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission", name=op.f("pk_role_permissions")),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name=op.f("fk_role_permissions_role_id")),
        sa.CheckConstraint(
            "length(trim(permission)) > 0", name=op.f("ck_role_permissions_permission_not_blank")
        ),
    )

    # System roles and their default permissions (plain SQL so the migration also renders offline).
    for code, (name, description, permissions) in ROLES.items():
        op.execute(
            "INSERT INTO roles (shop_id, code, name, description, is_system, is_active, created_at, updated_at) "
            f"VALUES (NULL, '{code}', '{name}', '{description}', {TRUE}, {TRUE}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        for permission in permissions:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                f"SELECT id, '{permission}' FROM roles WHERE shop_id IS NULL AND code = '{code}'"
            )

    op.create_table(
        "invitations",
        sa.Column("id", ID, nullable=False),
        sa.Column("shop_id", ID, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role_id", ID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "ACCEPTED",
                "REVOKED",
                "EXPIRED",
                name="invitation_status",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by", ID, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", ID, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_invitations_shop_id")),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name=op.f("fk_invitations_role_id")),
        sa.ForeignKeyConstraint(
            ["shop_id", "invited_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_invitations_shop_id_invited_by"),
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_invitations_shop_id_id")),
        sa.CheckConstraint("length(trim(email)) > 0", name=op.f("ck_invitations_email_not_blank")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_invitations_email_lowercase")),
    )
    op.create_index("ix_invitations_shop_status", "invitations", ["shop_id", "status"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", ID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("account_id", ID, nullable=False),
        sa.Column("user_id", ID, nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_auth_sessions_account_id")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index("ix_auth_sessions_account", "auth_sessions", ["account_id", "revoked_at"])
    op.create_index("ix_auth_sessions_expires", "auth_sessions", ["expires_at"])
    op.create_table(
        "background_jobs",
        sa.Column("id", ID, nullable=False),
        sa.Column("job_type", sa.String(60), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("shop_id", ID, nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "RETRYING",
                "CANCELLED",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(60), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error_message", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_background_jobs_shop_id")),
        sa.UniqueConstraint(
            "job_type", "idempotency_key", name=op.f("uq_background_jobs_job_type_idempotency_key")
        ),
        sa.CheckConstraint("length(trim(job_type)) > 0", name=op.f("ck_background_jobs_job_type_not_blank")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_background_jobs_attempts_non_negative")),
    )
    op.create_index("ix_background_jobs_due", "background_jobs", ["status", "run_after"])

    # users becomes the membership. SQLite rebuilds the table for this (batch mode).
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("account_id", ID, nullable=True))
        batch_op.add_column(sa.Column("role_id", ID, nullable=True))
        batch_op.add_column(
            sa.Column(
                "status",
                sa.Enum(
                    "INVITED",
                    "ACTIVE",
                    "SUSPENDED",
                    "REMOVED",
                    name="membership_status",
                    native_enum=False,
                    create_constraint=True,
                    length=9,
                ),
                server_default="ACTIVE",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("invited_by", ID, nullable=True))
        batch_op.add_column(sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.create_unique_constraint("uq_users_shop_id_email", ["shop_id", "email"])
        batch_op.create_unique_constraint("uq_users_account_id_shop_id", ["account_id", "shop_id"])
        batch_op.create_foreign_key("fk_users_account_id", "accounts", ["account_id"], ["id"])
        batch_op.create_foreign_key("fk_users_role_id", "roles", ["role_id"], ["id"])

    # Every existing user becomes an account and a membership. Nothing is removed.
    op.execute(
        "INSERT INTO accounts (email, full_name, phone, password_hash, status, failed_logins, created_at, updated_at) "
        "SELECT email, full_name, phone, password_hash, 'ACTIVE', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM users"
    )
    op.execute(
        "UPDATE users SET "
        "account_id = (SELECT a.id FROM accounts a WHERE a.email = users.email), "
        "role_id = (SELECT r.id FROM roles r WHERE r.shop_id IS NULL AND r.code = "
        "CASE WHEN users.role = 'OWNER' THEN 'OWNER' ELSE 'CASHIER' END), "
        "status = CASE WHEN users.is_active THEN 'ACTIVE' ELSE 'SUSPENDED' END, "
        "joined_at = users.created_at"
    )


def downgrade() -> None:
    if not op.get_context().as_sql:
        duplicates = (
            op.get_bind()
            .execute(sa.text("SELECT email FROM users GROUP BY email HAVING count(*) > 1"))
            .first()
        )
        if duplicates is not None:
            raise RuntimeError(
                "Cannot downgrade: the same email belongs to more than one shop. Remove the duplicate memberships first."
            )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("membership_status", type_="check")
        batch_op.drop_constraint("fk_users_role_id", type_="foreignkey")
        batch_op.drop_constraint("fk_users_account_id", type_="foreignkey")
        batch_op.drop_constraint("uq_users_account_id_shop_id", type_="unique")
        batch_op.drop_constraint("uq_users_shop_id_email", type_="unique")
        batch_op.create_unique_constraint("uq_users_email", ["email"])
        for column in (
            "last_active_at",
            "removed_at",
            "joined_at",
            "invited_by",
            "status",
            "role_id",
            "account_id",
        ):
            batch_op.drop_column(column)
    for table in ("background_jobs", "auth_sessions", "invitations", "role_permissions", "roles", "accounts"):
        op.drop_table(table)
