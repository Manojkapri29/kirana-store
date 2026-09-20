"""Shop expenses (rent, electricity, salaries...). Database foundation only; workflows arrive in Phase 11."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money
from app.models.base import (
    Base,
    DocumentLifecycleMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
    void_requires_reason,
)
from app.models.enums import PaymentMethod


class ExpenseCategory(TimestampMixin, Base):
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


class Expense(DocumentLifecycleMixin, TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("replaces_id"),
        tenant_fk("category_id", "expense_categories"),
        tenant_fk("replaces_id", "expenses"),
        tenant_fk("created_by", "users"),
        Index("ix_expenses_shop_date", "shop_id", "expense_date"),
        positive("amount"),
        void_requires_reason(),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    expense_date: Mapped[date] = mapped_column(Date)
    category_id: Mapped[int] = mapped_column(IdType)
    description: Mapped[str | None] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[PaymentMethod] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    replaces_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)
