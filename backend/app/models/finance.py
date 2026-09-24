"""Finance, accounting and business control (Phase 15).

There is deliberately no second accounting universe here. Sales, purchases, returns and khata payments stay in
the tables that already own them; `finance_ledger_service` reads them through one normalised view. This module
holds only what those tables cannot express:

* `finance_entries`: INSERT-ONLY money events finance itself records (an expense posting, a supplier payment,
  owner capital or withdrawal, other income, an adjustment) and their reversals. History is never edited: a
  correction is a new row that reverses or adjusts.
* `financial_periods`: OPEN / LOCKED / CLOSED date ranges that gate modification of past records.
* `cash_counts`: INSERT-ONLY physical cash counts (expected vs. actual); expected is never rewritten.
* `reconciliation_marks`: INSERT-ONLY review history for payment records; the newest mark is the status.
* `tax_settings` / `tax_rates`: a configurable tax *reporting* foundation; no jurisdiction is built in.
* `finance_settings`: per-shop approval thresholds and alert sensitivities.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import (
    CashFlowClass,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PeriodStatus,
    ReconStatus,
    TaxType,
)


class FinanceEntry(CreatedAtMixin, Base):
    """One money event recorded by finance. INSERT-ONLY. A reversal is a row of the same event type in the
    opposite direction whose `reverses_entry_id` points at the original (and whose reference is REVERSAL)."""

    __tablename__ = "finance_entries"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint(
            "shop_id", "event_type", "reference_type", "reference_id", name="uq_finance_entries_reference"
        ),
        UniqueConstraint("reverses_entry_id", name="uq_finance_entries_reverses"),
        tenant_fk("reverses_entry_id", "finance_entries"),
        tenant_fk("supplier_id", "suppliers"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("created_by", "users"),
        Index("ix_finance_entries_shop_date", "shop_id", "entry_date"),
        positive("amount"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    event_type: Mapped[FinanceEventType] = mapped_column(enum_type(FinanceEventType, "finance_event_type"))
    direction: Mapped[FlowDirection] = mapped_column(enum_type(FlowDirection, "flow_direction"))
    amount: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[FinancePaymentMethod] = mapped_column(
        enum_type(FinancePaymentMethod, "finance_payment_method")
    )
    entry_date: Mapped[date] = mapped_column(Date)
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[int | None] = mapped_column(IdType)
    supplier_id: Mapped[int | None] = mapped_column(IdType)
    customer_id: Mapped[int | None] = mapped_column(IdType)
    cash_flow_class: Mapped[CashFlowClass | None] = mapped_column(enum_type(CashFlowClass, "cash_flow_class"))
    reverses_entry_id: Mapped[int | None] = mapped_column(IdType)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(IdType)


class FinancialPeriod(TimestampMixin, Base):
    __tablename__ = "financial_periods"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "period_start"),
        tenant_fk("created_by", "users"),
        tenant_fk("status_changed_by", "users"),
        CheckConstraint("period_end >= period_start", name="period_dates_ordered"),
        Index("ix_financial_periods_shop_range", "shop_id", "period_start", "period_end"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[PeriodStatus] = mapped_column(
        enum_type(PeriodStatus, "period_status"),
        default=PeriodStatus.OPEN,
        server_default=PeriodStatus.OPEN.value,
    )
    status_changed_by: Mapped[int | None] = mapped_column(IdType)
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(IdType)


class CashCount(CreatedAtMixin, Base):
    """A physical count of the cash drawer at the end of `count_date`. INSERT-ONLY."""

    __tablename__ = "cash_counts"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("counted_by", "users"),
        Index("ix_cash_counts_shop_date", "shop_id", "count_date"),
        non_negative("actual_cash"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    count_date: Mapped[date] = mapped_column(Date)
    # NULL when there was no earlier count to roll forward from: never guessed, never zero.
    expected_cash: Mapped[Decimal | None] = mapped_column(Money)
    actual_cash: Mapped[Decimal] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    counted_by: Mapped[int] = mapped_column(IdType)
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ReconciliationMark(CreatedAtMixin, Base):
    """A person's review of one payment record. INSERT-ONLY: the newest mark for a record is its status."""

    __tablename__ = "reconciliation_marks"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("created_by", "users"),
        Index("ix_reconciliation_marks_shop_source", "shop_id", "source_type", "source_id"),
        not_blank("source_type"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    source_type: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[int] = mapped_column(IdType)
    status: Mapped[ReconStatus] = mapped_column(enum_type(ReconStatus, "recon_status"))
    confirmed_amount: Mapped[Decimal | None] = mapped_column(Money)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(IdType)


class TaxSetting(TimestampMixin, Base):
    """One per shop. Says which kind of tax the shop reports and how its prices are quoted."""

    __tablename__ = "tax_settings"
    __table_args__ = (UniqueConstraint("shop_id"), UniqueConstraint("shop_id", "id"))

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    tax_type: Mapped[TaxType] = mapped_column(enum_type(TaxType, "tax_type"))
    registration_number: Mapped[str | None] = mapped_column(String(30))
    location_state: Mapped[str | None] = mapped_column(String(60))
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class TaxRate(TimestampMixin, Base):
    """A configurable rate in basis points (1800 = 18.00%). `category_id` ties it to a product category;
    NULL is the shop's default rate for anything without its own."""

    __tablename__ = "tax_rates"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "name"),
        tenant_fk("category_id", "categories"),
        CheckConstraint("rate_bp >= 0 AND rate_bp <= 10000", name="rate_bp_in_range"),
        not_blank("name"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(60))
    rate_bp: Mapped[int] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(IdType)
    tax_category: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class FinanceSettings(TimestampMixin, Base):
    """One per shop. Thresholds left NULL switch that extra control off: there is no built-in number for an
    approval limit. The alert sensitivities carry visible defaults a shop can change."""

    __tablename__ = "finance_settings"
    __table_args__ = (
        UniqueConstraint("shop_id"),
        UniqueConstraint("shop_id", "id"),
        non_negative("expense_approval_threshold"),
        non_negative("adjustment_approval_threshold"),
        non_negative("cash_adjustment_threshold"),
        non_negative("cash_variance_alert_amount"),
        positive("overdue_after_days"),
        non_negative("expense_spike_pct"),
        non_negative("margin_drop_points"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    expense_approval_threshold: Mapped[Decimal | None] = mapped_column(Money)
    adjustment_approval_threshold: Mapped[Decimal | None] = mapped_column(Money)
    cash_adjustment_threshold: Mapped[Decimal | None] = mapped_column(Money)
    period_reopen_requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=expression.false()
    )
    overdue_after_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    expense_spike_pct: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    margin_drop_points: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    cash_variance_alert_amount: Mapped[Decimal] = mapped_column(
        Money, default=Decimal("0"), server_default="0"
    )
