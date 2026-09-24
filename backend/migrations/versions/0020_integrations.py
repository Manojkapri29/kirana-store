"""Integrations and ecosystem (Phase 17)

* `integrations`: per-shop provider configuration (no secrets: only the NAME of an environment variable), `integration_events`
  (a payload-free call log), `online_payments` and `online_payment_events`, `webhook_events` (verified, unique per event id),
  `message_deliveries`, `accounting_mappings`.
* Seeds the integration permissions into the system roles.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-24 12:07:35.787551
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_PERMISSIONS = {
    "OWNER": [
        "INTEGRATION_VIEW",
        "INTEGRATION_MANAGE",
        "INTEGRATION_CONFIGURE",
        "INTEGRATION_TEST",
        "INTEGRATION_LOGS",
        "PAYMENT_INTEGRATION_MANAGE",
        "NOTIFICATION_INTEGRATION_MANAGE",
        "STORAGE_INTEGRATION_MANAGE",
        "WEBHOOK_MANAGE",
    ],
    "MANAGER": [
        "INTEGRATION_VIEW",
        "INTEGRATION_TEST",
        "INTEGRATION_LOGS",
        "PAYMENT_INTEGRATION_MANAGE",
        "NOTIFICATION_INTEGRATION_MANAGE",
    ],
    "ACCOUNTANT": ["INTEGRATION_VIEW"],
}


def upgrade() -> None:
    op.create_table(
        "accounting_mappings",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("source_key", sa.String(length=60), nullable=False),
        sa.Column("external_code", sa.String(length=60), nullable=False),
        sa.Column("external_name", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(external_code)) > 0", name=op.f("ck_accounting_mappings_external_code_not_blank")
        ),
        sa.CheckConstraint(
            "length(trim(source_key)) > 0", name=op.f("ck_accounting_mappings_source_key_not_blank")
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_accounting_mappings_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounting_mappings")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_accounting_mappings_shop_id_id")),
        sa.UniqueConstraint("shop_id", "source_key", name=op.f("uq_accounting_mappings_shop_id_source_key")),
    )
    op.create_table(
        "integration_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "integration_type",
            sa.Enum(
                "PAYMENT",
                "EMAIL",
                "SMS",
                "WHATSAPP",
                "PUSH",
                "STORAGE",
                "MAPS",
                "PRODUCT_DATA",
                "ACCOUNTING",
                name="integration_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_integration_events_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_events")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_integration_events_shop_id_id")),
    )
    with op.batch_alter_table("integration_events", schema=None) as batch_op:
        batch_op.create_index("ix_integration_events_shop_created", ["shop_id", "created_at"], unique=False)

    op.create_table(
        "message_deliveries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column(
            "channel",
            sa.Enum(
                "IN_APP",
                "EMAIL",
                "SMS",
                "WHATSAPP",
                "PUSH",
                name="message_channel",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "TRANSACTIONAL", "MARKETING", name="message_kind", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "SENT",
                "FAILED",
                "NOT_CONFIGURED",
                "SKIPPED_NO_CONSENT",
                "SKIPPED_NO_CONTACT",
                name="message_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("recipient_masked", sa.String(length=80), nullable=True),
        sa.Column("subject", sa.String(length=150), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("campaign_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_message_deliveries_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_message_deliveries_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_deliveries")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_message_deliveries_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "idempotency_key", name=op.f("uq_message_deliveries_shop_id_idempotency_key")
        ),
    )
    with op.batch_alter_table("message_deliveries", schema=None) as batch_op:
        batch_op.create_index("ix_message_deliveries_shop_status", ["shop_id", "status"], unique=False)

    op.create_table(
        "integrations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "integration_type",
            sa.Enum(
                "PAYMENT",
                "EMAIL",
                "SMS",
                "WHATSAPP",
                "PUSH",
                "STORAGE",
                "MAPS",
                "PRODUCT_DATA",
                "ACCOUNTING",
                name="integration_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "NOT_CONFIGURED",
                "CONFIGURED",
                "DISABLED",
                "ERROR",
                name="integration_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("credential_ref", sa.String(length=100), nullable=True),
        sa.Column("webhook_key", sa.String(length=64), nullable=True),
        sa.Column("webhook_credential_ref", sa.String(length=100), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=60), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(provider)) > 0", name=op.f("ck_integrations_provider_not_blank")),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_integrations_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_integrations_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_integrations_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "integration_type", name=op.f("uq_integrations_shop_id_integration_type")
        ),
        sa.UniqueConstraint("webhook_key", name=op.f("uq_integrations_webhook_key")),
    )
    op.create_table(
        "online_payments",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column(
            "method",
            sa.Enum(
                "UPI",
                "CARD",
                "PAYMENT_LINK",
                "ONLINE_PAYMENT",
                "COD",
                name="online_payment_method",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            sa.Enum(
                "SALE_PAYMENT",
                "KHATA_PAYMENT",
                name="online_payment_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "PENDING",
                "AUTHORIZED",
                "CAPTURED",
                "FAILED",
                "CANCELLED",
                "REFUNDED",
                "PARTIALLY_REFUNDED",
                name="online_payment_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("refunded_amount", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("sale_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("quick_sale_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("order_ref", sa.String(length=60), nullable=True),
        sa.Column("provider_txn_id", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("review_reason", sa.String(length=200), nullable=True),
        sa.Column("khata_entry_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=60), nullable=True),
        sa.Column("created_by", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_online_payments_amount_positive")),
        sa.CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name=op.f("ck_online_payments_refund_within_amount"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "created_by"],
            ["users.shop_id", "users.id"],
            name=op.f("fk_online_payments_shop_id_created_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "customer_id"],
            ["customers.shop_id", "customers.id"],
            name=op.f("fk_online_payments_shop_id_customer_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "quick_sale_id"],
            ["quick_sales.shop_id", "quick_sales.id"],
            name=op.f("fk_online_payments_shop_id_quick_sale_id"),
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "sale_id"],
            ["sales.shop_id", "sales.id"],
            name=op.f("fk_online_payments_shop_id_sale_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_online_payments_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_online_payments")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_online_payments_shop_id_id")),
        sa.UniqueConstraint(
            "shop_id", "idempotency_key", name=op.f("uq_online_payments_shop_id_idempotency_key")
        ),
        sa.UniqueConstraint(
            "shop_id",
            "provider",
            "provider_txn_id",
            name=op.f("uq_online_payments_shop_id_provider_provider_txn_id"),
        ),
    )
    with op.batch_alter_table("online_payments", schema=None) as batch_op:
        batch_op.create_index("ix_online_payments_shop_status", ["shop_id", "status"], unique=False)

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("integration_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "PROCESSING",
                "PROCESSED",
                "FAILED",
                "IGNORED",
                name="webhook_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("duplicates", sa.Integer(), server_default="0", nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "integration_id"],
            ["integrations.shop_id", "integrations.id"],
            name=op.f("fk_webhook_events_shop_id_integration_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_webhook_events_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_webhook_events_shop_id_id")),
        sa.UniqueConstraint(
            "integration_id", "event_id", name=op.f("uq_webhook_events_integration_id_event_id")
        ),
    )
    with op.batch_alter_table("webhook_events", schema=None) as batch_op:
        batch_op.create_index("ix_webhook_events_shop_received", ["shop_id", "received_at"], unique=False)

    op.create_table(
        "online_payment_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("shop_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("payment_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("provider_event_id", sa.String(length=120), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id", "payment_id"],
            ["online_payments.shop_id", "online_payments.id"],
            name=op.f("fk_online_payment_events_shop_id_payment_id"),
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name=op.f("fk_online_payment_events_shop_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_online_payment_events")),
        sa.UniqueConstraint("shop_id", "id", name=op.f("uq_online_payment_events_shop_id_id")),
    )
    with op.batch_alter_table("online_payment_events", schema=None) as batch_op:
        batch_op.create_index("ix_online_payment_events_payment", ["shop_id", "payment_id"], unique=False)

    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "INSERT INTO role_permissions (role_id, permission) "
                f"SELECT id, '{code}' FROM roles WHERE shop_id IS NULL AND code = '{role_code}'"
            )


def downgrade() -> None:
    for role_code, codes in NEW_PERMISSIONS.items():
        for code in codes:
            op.execute(
                "DELETE FROM role_permissions WHERE permission = "
                f"'{code}' AND role_id IN (SELECT id FROM roles WHERE shop_id IS NULL AND code = '{role_code}')"
            )
    with op.batch_alter_table("online_payment_events", schema=None) as batch_op:
        batch_op.drop_index("ix_online_payment_events_payment")

    op.drop_table("online_payment_events")
    with op.batch_alter_table("webhook_events", schema=None) as batch_op:
        batch_op.drop_index("ix_webhook_events_shop_received")

    op.drop_table("webhook_events")
    with op.batch_alter_table("online_payments", schema=None) as batch_op:
        batch_op.drop_index("ix_online_payments_shop_status")

    op.drop_table("online_payments")
    op.drop_table("integrations")
    with op.batch_alter_table("message_deliveries", schema=None) as batch_op:
        batch_op.drop_index("ix_message_deliveries_shop_status")

    op.drop_table("message_deliveries")
    with op.batch_alter_table("integration_events", schema=None) as batch_op:
        batch_op.drop_index("ix_integration_events_shop_created")

    op.drop_table("integration_events")
    op.drop_table("accounting_mappings")
