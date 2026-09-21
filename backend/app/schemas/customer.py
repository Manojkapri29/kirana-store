from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.enums import CustomerLedgerEntryType, KhataReferenceType, PaymentMethod
from app.schemas.common import MoneyIn, Page
from app.services.customer_service import SaveResult
from app.services.khata_service import BalanceStatus, CustomerAccount, EntryResult, LedgerRow

# Only lengths are limited here. Phone and email formats are checked by the service, which gives clear
# per-field messages and stores each in one consistent form.
Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
EntryNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None
    notes: Notes | None = None
    # What the customer already owes when they are added. It becomes an OPENING_BALANCE ledger entry.
    opening_balance: MoneyIn | None = None
    opening_balance_date: date | None = None


class CustomerUpdate(BaseModel):
    """Partial update: only the fields sent change. Send `null` (or empty text) to clear an optional one."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None
    notes: Notes | None = None


class BalanceOut(BaseModel):
    """What the ledger says: `balance` is signed (positive = owes, negative = advance)."""

    customer_id: int
    balance: Decimal
    outstanding: Decimal
    advance: Decimal
    status: BalanceStatus

    @classmethod
    def from_account(cls, account: CustomerAccount) -> "BalanceOut":
        return cls(
            customer_id=account.customer.id,
            balance=account.balance,
            outstanding=account.outstanding,
            advance=account.advance,
            status=account.status,
        )


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    balance: Decimal
    outstanding: Decimal
    advance: Decimal
    balance_status: BalanceStatus
    entry_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_account(cls, account: CustomerAccount) -> "CustomerOut":
        c = account.customer
        return cls(
            id=c.id,
            name=c.name,
            phone=c.phone,
            email=c.email,
            address=c.address,
            notes=c.notes,
            is_active=c.is_active,
            balance=account.balance,
            outstanding=account.outstanding,
            advance=account.advance,
            balance_status=account.status,
            entry_count=account.entry_count,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class LedgerEntryOut(BaseModel):
    id: int
    customer_id: int
    entry_date: date
    entry_type: CustomerLedgerEntryType
    amount_delta: Decimal
    balance_after: Decimal
    payment_method: PaymentMethod | None
    payment_reference: str | None
    reference_type: KhataReferenceType | None
    reference_id: int | None
    reverses_entry_id: int | None
    reversed_by_entry_id: int | None
    note: str | None
    created_by_name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: LedgerRow) -> "LedgerEntryOut":
        return cls(**row.__dict__)


class LedgerListOut(Page):
    items: list[LedgerEntryOut]


class CustomerSaved(BaseModel):
    customer: CustomerOut
    warnings: list[str]
    opening_entry: LedgerEntryOut | None = None

    @classmethod
    def from_result(
        cls, account: CustomerAccount, result: SaveResult, opening: EntryResult | None
    ) -> "CustomerSaved":
        return cls(
            customer=CustomerOut.from_account(account),
            warnings=result.warnings,
            opening_entry=None if opening is None else LedgerEntryOut.from_row(opening.entry),
        )


class CustomerListOut(Page):
    items: list[CustomerOut]


class BalanceFilter(StrEnum):
    ANY = "any"
    OUTSTANDING = "outstanding"
    SETTLED = "settled"
    ADVANCE = "advance"


class CustomerSort(StrEnum):
    NAME = "name"
    BALANCE = "balance"  # the largest amounts owed first


class OpeningBalanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    entry_date: date | None = None
    note: EntryNote | None = None


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    entry_date: date | None = None  # defaults to today in the shop's timezone
    payment_method: PaymentMethod | None = None
    payment_reference: Reference | None = None
    note: EntryNote | None = None


class AdjustmentDirection(StrEnum):
    INCREASE = "INCREASE"  # the customer owes more
    DECREASE = "DECREASE"  # the customer owes less


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn  # always positive; `direction` says which way
    direction: AdjustmentDirection
    reason: Reason
    entry_date: date | None = None


class ReverseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class KhataEntryResult(BaseModel):
    """The entry just written, and the customer's balance right after it."""

    entry: LedgerEntryOut
    balance: BalanceOut

    @classmethod
    def from_result(cls, result: EntryResult) -> "KhataEntryResult":
        return cls(
            entry=LedgerEntryOut.from_row(result.entry), balance=BalanceOut.from_account(result.account)
        )
