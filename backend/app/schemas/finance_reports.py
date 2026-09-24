"""Response shapes for the finance reports. Every report says what it is built from and what it cannot tell you;
a figure that cannot be known is null (never zero) and accompanied by a plain-words reason."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReconStatus, TaxType
from app.schemas.common import MoneyIn


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Cash ---------------------------------------------------------------------------------------------------------


class CashCountOut(_Out):
    id: int
    count_date: date
    expected_cash: Decimal | None
    actual_cash: Decimal
    difference: Decimal | None
    reason: str | None
    counted_by: int
    counted_at: datetime


class CashCountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count_date: date
    actual_cash: MoneyIn
    reason: str | None = Field(default=None, max_length=500)


class CashSummaryOut(_Out):
    day: date
    opening_cash: Decimal | None
    lines: dict[str, Decimal]
    net_movement: Decimal
    expected_closing: Decimal | None
    opening_basis: str
    unclassified_receipts: Decimal
    latest_count: CashCountOut | None
    notes: list[str]


# --- Payables and receivables -----------------------------------------------------------------------------------------


class SupplierPayableOut(_Out):
    supplier_id: int
    name: str
    total_purchases: Decimal
    purchase_returns: Decimal
    payments_made: Decimal
    refunds_received: Decimal
    balance: Decimal
    advance: Decimal
    aging: dict[str, Decimal]
    oldest_open_date: date | None


class PayablesOut(_Out):
    as_of: date
    suppliers: list[SupplierPayableOut]
    total_payable: Decimal
    total_advances: Decimal
    total_purchases: Decimal
    total_payments: Decimal
    total_returns: Decimal
    aging: dict[str, Decimal]
    methodology: str
    notes: list[str]


class CustomerReceivableOut(_Out):
    customer_id: int
    name: str
    balance: Decimal
    advance: Decimal
    aging: dict[str, Decimal]
    oldest_open_date: date | None
    oldest_open_days: int | None
    last_payment_date: date | None


class ReceivablesOut(_Out):
    as_of: date
    customers: list[CustomerReceivableOut]
    total_receivables: Decimal
    total_advances: Decimal
    aging: dict[str, Decimal]
    credit_sales: Decimal
    payments_received: Decimal
    return_credits: Decimal
    period_start: date | None
    period_end: date | None
    methodology: str
    overdue: str
    notes: list[str]


# --- Tax configuration ----------------------------------------------------------------------------------------------


class TaxSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_type: TaxType
    registration_number: str | None = Field(default=None, max_length=30)
    location_state: str | None = Field(default=None, max_length=60)
    prices_include_tax: bool = True


class TaxSettingsOut(_Out):
    tax_type: TaxType
    registration_number: str | None
    location_state: str | None
    prices_include_tax: bool


class TaxRateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    rate_percent: Decimal = Field(ge=0, le=100, decimal_places=2)
    category_id: int | None = None
    tax_category: str | None = Field(default=None, max_length=60)


class TaxRatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    is_active: bool | None = None
    tax_category: str | None = Field(default=None, max_length=60)


class TaxRateOut(_Out):
    id: int
    name: str
    rate_bp: int
    rate_percent: Decimal
    category_id: int | None
    tax_category: str | None
    is_active: bool

    @classmethod
    def of(cls, r) -> "TaxRateOut":  # noqa: ANN001
        return cls(
            id=r.id,
            name=r.name,
            rate_bp=r.rate_bp,
            rate_percent=Decimal(r.rate_bp) / 100,
            category_id=r.category_id,
            tax_category=r.tax_category,
            is_active=r.is_active,
        )


# --- Reconciliation ---------------------------------------------------------------------------------------------------


class ReconMarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(min_length=1, max_length=30)
    source_id: int
    status: ReconStatus
    confirmed_amount: MoneyIn | None = None
    note: str | None = Field(default=None, max_length=500)


class ReconMarkOut(_Out):
    id: int
    source_type: str
    source_id: int
    status: ReconStatus
    confirmed_amount: Decimal | None
    note: str | None
    created_by: int
    created_at: datetime
