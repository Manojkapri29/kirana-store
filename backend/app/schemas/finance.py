"""Request and response shapes for the finance API. Money is a string in JSON (a Decimal with 2 places)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import Expense, ExpenseCategory, FinancialPeriod
from app.models.enums import (
    CashFlowClass,
    ExpenseStatus,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PeriodStatus,
)
from app.schemas.common import MoneyIn, Page
from app.services.finance_ledger_service import LedgerRow

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


# --- Settings ----------------------------------------------------------------------------------------------------


class SettingsIO(BaseModel):
    """None on a threshold = that extra control is off. There is no built-in number."""

    model_config = ConfigDict(extra="forbid")

    expense_approval_threshold: MoneyIn | None = None
    adjustment_approval_threshold: MoneyIn | None = None
    cash_adjustment_threshold: MoneyIn | None = None
    period_reopen_requires_approval: bool = False
    overdue_after_days: int = Field(default=30, ge=1)
    expense_spike_pct: int = Field(default=50, ge=0)
    margin_drop_points: int = Field(default=5, ge=0)
    cash_variance_alert_amount: MoneyIn = Decimal("0")


# --- Expense categories and expenses -------------------------------------------------------------------------------


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    cash_flow_class: CashFlowClass | None = None


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    is_active: bool | None = None
    cash_flow_class: CashFlowClass | None = None


class CategoryOut(BaseModel):
    id: int
    name: str
    is_active: bool
    cash_flow_class: CashFlowClass | None

    @classmethod
    def of(cls, c: ExpenseCategory) -> "CategoryOut":
        return cls(id=c.id, name=c.name, is_active=c.is_active, cash_flow_class=c.cash_flow_class)


class ExpenseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date
    category_id: int
    amount: MoneyIn
    payment_method: FinancePaymentMethod
    payee: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    attachment_ref: str | None = Field(default=None, max_length=300)
    notes: Text | None = None


class ExpensePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date | None = None
    category_id: int | None = None
    amount: MoneyIn | None = None
    payment_method: FinancePaymentMethod | None = None
    payee: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    attachment_ref: str | None = Field(default=None, max_length=300)
    notes: Text | None = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ExpenseOut(BaseModel):
    id: int
    expense_no: str
    expense_date: date
    category_id: int
    amount: Decimal
    payment_method: FinancePaymentMethod
    payee: str | None
    description: str | None
    attachment_ref: str | None
    notes: str | None
    status: ExpenseStatus
    requires_approval: bool
    submitted_at: datetime | None
    approved_by: int | None
    approved_at: datetime | None
    rejection_reason: str | None
    posted_by: int | None
    posted_at: datetime | None
    void_reason: str | None
    voided_at: datetime | None
    created_by: int
    created_at: datetime

    @classmethod
    def of(cls, e: Expense) -> "ExpenseOut":
        return cls.model_validate(e, from_attributes=True)


class ExpenseListOut(Page):
    items: list[ExpenseOut]


# --- Ledger --------------------------------------------------------------------------------------------------------


class LedgerRowOut(BaseModel):
    key: str
    entry_date: date
    event_type: FinanceEventType
    source_module: str
    source_type: str
    source_id: int
    reference: str | None
    amount: Decimal
    settled_amount: Decimal
    direction: FlowDirection
    payment_method: str
    customer_id: int | None
    supplier_id: int | None
    status: str
    created_by: int | None
    cash_flow_class: CashFlowClass | None
    note: str | None

    @classmethod
    def of(cls, r: LedgerRow) -> "LedgerRowOut":
        return cls(**r.__dict__)


class LedgerListOut(Page):
    items: list[LedgerRowOut]
    total_in: Decimal
    total_out: Decimal


class EntryIn(BaseModel):
    """A money event finance itself records. Only these four types are recorded by hand: sales, purchases,
    returns and customer payments come from their own documents, and adjustments have their own endpoint."""

    model_config = ConfigDict(extra="forbid")

    event_type: FinanceEventType
    amount: MoneyIn
    payment_method: FinancePaymentMethod
    entry_date: date
    supplier_id: int | None = None
    note: str | None = Field(default=None, max_length=500)
    cash_flow_class: CashFlowClass | None = None


class EntryOut(BaseModel):
    id: int
    event_type: FinanceEventType
    direction: FlowDirection
    amount: Decimal
    payment_method: FinancePaymentMethod
    entry_date: date
    reference_type: str | None
    reference_id: int | None
    supplier_id: int | None
    customer_id: int | None
    cash_flow_class: CashFlowClass | None
    reverses_entry_id: int | None
    note: str | None
    created_by: int
    created_at: datetime

    @classmethod
    def of(cls, e: Any) -> "EntryOut":
        return cls.model_validate(e, from_attributes=True)


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: FlowDirection
    amount: MoneyIn
    payment_method: FinancePaymentMethod
    entry_date: date
    note: Reason
    approval_request_id: int | None = None


class ApprovalRequiredOut(BaseModel):
    """Returned (202) instead of an entry when the change is waiting on a second person's approval."""

    status: str = "APPROVAL_REQUIRED"
    approval_request_id: int


# --- Periods -------------------------------------------------------------------------------------------------------


class PeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    note: str | None = Field(default=None, max_length=500)


class PeriodOut(BaseModel):
    id: int
    period_start: date
    period_end: date
    status: PeriodStatus
    status_changed_by: int | None
    status_changed_at: datetime | None
    note: str | None
    reopen_approval_pending: bool = False

    @classmethod
    def of(cls, p: FinancialPeriod, *, reopen_pending: bool = False) -> "PeriodOut":
        return cls(
            id=p.id, period_start=p.period_start, period_end=p.period_end, status=p.status,
            status_changed_by=p.status_changed_by, status_changed_at=p.status_changed_at, note=p.note,
            reopen_approval_pending=reopen_pending,
        )  # fmt: skip
