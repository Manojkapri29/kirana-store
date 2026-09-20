"""Declarative base, shared column helpers, and reusable constraint builders.

Tenant safety (multi-shop)
--------------------------
Every shop-owned table has `shop_id`, and it also has `UNIQUE (shop_id, id)`. A reference from one
shop-owned table to another is a *composite* foreign key `(shop_id, x_id) -> parent(shop_id, id)`
(see `tenant_fk`). The database itself then refuses a row in Shop A that points at a row in Shop B, so
tenant isolation does not rely on every query remembering a WHERE clause. Queries must still filter by
`shop_id`, but a bug there can no longer corrupt data across shops.

Nullable references (e.g. an optional customer) stay safe: when the id is NULL the constraint is not
checked, and when it is set, `shop_id` must match the parent's.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCDateTime, utc_now
from app.models.enums import DocumentStatus

# Explicit, deterministic constraint names. Required for reliable Alembic migrations (especially on
# SQLite) and for readable errors. PostgreSQL limits identifiers to 63 characters; a test enforces it.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

# BIGINT on PostgreSQL. SQLite only auto-generates ids for a column typed exactly INTEGER
# (its rowid alias), so it gets INTEGER, which is already 64-bit there.
IdType = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    """For insert-only tables (ledgers, audit log): a creation time and nothing to update."""

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


def id_column() -> Mapped[int]:
    return mapped_column(IdType, primary_key=True)


def shop_id_column() -> Mapped[int]:
    return mapped_column(IdType, ForeignKey("shops.id"), nullable=False)


def enum_type(enum_cls: Any, name: str) -> Enum:
    """Text column with a CHECK constraint listing the allowed values (portable, no native ENUM)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=max(len(member.value) for member in enum_cls),
        values_callable=lambda members: [member.value for member in members],
    )


# --- Constraint builders -------------------------------------------------------------------------


def tenant_fk(column: str, parent_table: str) -> ForeignKeyConstraint:
    """Composite foreign key that also pins the row to the same shop as its parent."""
    return ForeignKeyConstraint(["shop_id", column], [f"{parent_table}.shop_id", f"{parent_table}.id"])


def not_blank(column: str) -> CheckConstraint:
    return CheckConstraint(f"length(trim({column})) > 0", name=f"{column}_not_blank")


def non_negative(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")


def positive(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} > 0", name=f"{column}_positive")


def all_or_none(name: str, *columns: str) -> CheckConstraint:
    """Either every listed column is NULL, or none of them is."""
    all_null = " AND ".join(f"{c} IS NULL" for c in columns)
    none_null = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    return CheckConstraint(f"({all_null}) OR ({none_null})", name=name)


def void_requires_reason() -> CheckConstraint:
    return CheckConstraint("status = 'POSTED' OR void_reason IS NOT NULL", name="void_needs_reason")


def payment_rules(*, total_must_be_positive: bool = False) -> tuple[CheckConstraint, ...]:
    """Shared PAID/CREDIT rules for `sales` and `quick_sales` (BUSINESS_RULES K3).

    PAID   -> the full amount was received.
    CREDIT -> a customer is recorded (their khata is charged) and something is still owed.
    Any money received needs a payment method.
    """
    return (
        positive("total_amount") if total_must_be_positive else non_negative("total_amount"),
        non_negative("amount_paid"),
        CheckConstraint("payment_type <> 'PAID' OR amount_paid = total_amount", name="paid_means_fully_paid"),
        CheckConstraint(
            "payment_type <> 'CREDIT' OR (customer_id IS NOT NULL AND amount_paid < total_amount)",
            name="credit_needs_customer_and_balance",
        ),
        CheckConstraint("amount_paid = 0 OR payment_method IS NOT NULL", name="payment_needs_method"),
    )


class DocumentLifecycleMixin:
    """Status columns shared by every business document (purchase, sale, expense, return...).

    Documents are never deleted. A wrong one is voided (status VOID + reason) and, if it needs
    correcting, replaced by a new document that points back through `replaces_id` (BUSINESS_RULES E1).
    """

    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "status"),
        default=DocumentStatus.POSTED,
        server_default=DocumentStatus.POSTED.value,
        nullable=False,
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
