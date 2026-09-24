"""Shop expenses (Phase 15). An expense moves DRAFT -> SUBMITTED -> APPROVED -> POSTED; only a POSTED one
reaches the financial reports, and a wrong one is VOIDED by a reversing entry in `finance_entries`, never
deleted."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import CashFlowClass, ExpenseStatus, FinancePaymentMethod


class ExpenseCategory(TimestampMixin, Base):
    """Configurable per shop; nothing is hardcoded. `cash_flow_class` is optional: an expense with no class is
    reported as OPERATING, but a category can say INVESTING, FINANCING or OTHER where that is justified."""

    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("shop_id", "name"),
        UniqueConstraint("shop_id", "id"),
        not_blank("name"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    cash_flow_class: Mapped[CashFlowClass | None] = mapped_column(enum_type(CashFlowClass, "cash_flow_class"))


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "expense_no"),
        UniqueConstraint("replaces_id"),
        tenant_fk("category_id", "expense_categories"),
        tenant_fk("replaces_id", "expenses"),
        tenant_fk("created_by", "users"),
        tenant_fk("approved_by", "users"),
        tenant_fk("posted_by", "users"),
        Index("ix_expenses_shop_date", "shop_id", "expense_date"),
        Index("ix_expenses_shop_status", "shop_id", "status"),
        positive("amount"),
        CheckConstraint("status <> 'VOIDED' OR void_reason IS NOT NULL", name="void_needs_reason"),
        CheckConstraint("status <> 'REJECTED' OR rejection_reason IS NOT NULL", name="reject_needs_reason"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    expense_no: Mapped[str] = mapped_column(String(30))  # e.g. EXP/2026-27/0001
    expense_date: Mapped[date] = mapped_column(Date)
    category_id: Mapped[int] = mapped_column(IdType)
    description: Mapped[str | None] = mapped_column(String(500))
    payee: Mapped[str | None] = mapped_column(String(150))
    amount: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[FinancePaymentMethod] = mapped_column(
        enum_type(FinancePaymentMethod, "finance_payment_method")
    )
    attachment_ref: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ExpenseStatus] = mapped_column(
        enum_type(ExpenseStatus, "expense_status"),
        default=ExpenseStatus.DRAFT,
        server_default=ExpenseStatus.DRAFT.value,
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false())
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approved_by: Mapped[int | None] = mapped_column(IdType)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    posted_by: Mapped[int | None] = mapped_column(IdType)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)
