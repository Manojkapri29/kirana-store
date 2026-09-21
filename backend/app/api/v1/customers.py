"""Customer and khata endpoints.

There is deliberately no DELETE (customers are deactivated) and no generic "insert a ledger row": money moves
only through the controlled operations below, each of which is a `khata_service` call. Ledger rows are never
edited; a mistake is undone with `/reverse` or `/adjustments`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.v1.products import StatusFilter, active_flag
from app.db.session import get_session, write_transaction
from app.models.enums import CustomerLedgerEntryType
from app.schemas.customer import (
    AdjustmentDirection,
    AdjustmentIn,
    BalanceFilter,
    BalanceOut,
    CustomerCreate,
    CustomerListOut,
    CustomerOut,
    CustomerSaved,
    CustomerSort,
    CustomerUpdate,
    KhataEntryResult,
    LedgerEntryOut,
    LedgerListOut,
    OpeningBalanceIn,
    PaymentIn,
    ReverseIn,
)
from app.services import customer_service, khata_service
from app.services.khata_service import BalanceStatus

router = APIRouter(prefix="/customers", tags=["customers"])
ReadSession = Annotated[Session, Depends(get_session)]

_BALANCE_FILTERS = {
    BalanceFilter.ANY: None,
    BalanceFilter.OUTSTANDING: BalanceStatus.OUTSTANDING,
    BalanceFilter.SETTLED: BalanceStatus.SETTLED,
    BalanceFilter.ADVANCE: BalanceStatus.ADVANCE,
}


@router.get("", response_model=CustomerListOut)
def list_customers(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None, Query(max_length=100, description="Search name, phone, email or address")
    ] = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    balance: Annotated[
        BalanceFilter, Query(description="Only customers who owe, are settled, or paid ahead")
    ] = (BalanceFilter.ANY),
    sort: CustomerSort = CustomerSort.NAME,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerListOut:
    accounts, total = khata_service.list_accounts(
        session,
        ctx.shop_id,
        q=q,
        active=active_flag(status),
        balance=_BALANCE_FILTERS[balance],
        biggest_first=sort is CustomerSort.BALANCE,
        limit=limit,
        offset=offset,
    )
    return CustomerListOut(
        items=[CustomerOut.from_account(a) for a in accounts], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=CustomerSaved, status_code=201)
def create_customer(payload: CustomerCreate, ctx: Ctx) -> CustomerSaved:
    """Create a customer, optionally with the amount they already owe (an opening balance)."""
    data = payload.model_dump(exclude={"opening_balance", "opening_balance_date"})
    with write_transaction() as session:
        saved, opening = khata_service.create_customer_with_opening_balance(
            session,
            ctx,
            data,
            opening_balance=payload.opening_balance,
            opening_date=payload.opening_balance_date,
        )
        account = khata_service.get_account(session, ctx.shop_id, saved.customer.id)
        return CustomerSaved.from_result(account, saved, opening)


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, ctx: Ctx, session: ReadSession) -> CustomerOut:
    return CustomerOut.from_account(khata_service.get_account(session, ctx.shop_id, customer_id))


@router.patch("/{customer_id}", response_model=CustomerSaved)
def update_customer(customer_id: int, payload: CustomerUpdate, ctx: Ctx) -> CustomerSaved:
    with write_transaction() as session:
        saved = customer_service.update_customer(
            session, ctx, customer_id, payload.model_dump(exclude_unset=True)
        )
        return CustomerSaved.from_result(
            khata_service.get_account(session, ctx.shop_id, customer_id), saved, None
        )


@router.post("/{customer_id}/activate", response_model=CustomerOut)
def activate_customer(customer_id: int, ctx: Ctx) -> CustomerOut:
    with write_transaction() as session:
        customer_service.set_customer_active(session, ctx, customer_id, active=True)
        return CustomerOut.from_account(khata_service.get_account(session, ctx.shop_id, customer_id))


@router.post("/{customer_id}/deactivate", response_model=CustomerOut)
def deactivate_customer(customer_id: int, ctx: Ctx) -> CustomerOut:
    with write_transaction() as session:
        customer_service.set_customer_active(session, ctx, customer_id, active=False)
        return CustomerOut.from_account(khata_service.get_account(session, ctx.shop_id, customer_id))


# --- Khata ---------------------------------------------------------------------------------------


@router.get("/{customer_id}/balance", response_model=BalanceOut)
def get_balance(customer_id: int, ctx: Ctx, session: ReadSession) -> BalanceOut:
    """The balance, derived from the ledger: positive = owes, negative = advance."""
    return BalanceOut.from_account(khata_service.get_account(session, ctx.shop_id, customer_id))


@router.get("/{customer_id}/ledger", response_model=LedgerListOut)
def get_ledger(
    customer_id: int,
    ctx: Ctx,
    session: ReadSession,
    entry_type: CustomerLedgerEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LedgerListOut:
    """The customer's history, newest first, with the running balance after each entry."""
    rows, total = khata_service.get_customer_ledger(
        session,
        ctx.shop_id,
        customer_id,
        entry_type=entry_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return LedgerListOut(
        items=[LedgerEntryOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/{customer_id}/opening-balance", response_model=KhataEntryResult, status_code=201)
def add_opening_balance(customer_id: int, payload: OpeningBalanceIn, ctx: Ctx) -> KhataEntryResult:
    """What the customer already owed before the shop started using the system. Once per customer."""
    with write_transaction() as session:
        result = khata_service.create_opening_balance(
            session, ctx, customer_id, payload.amount, entry_date=payload.entry_date, note=payload.note
        )
        return KhataEntryResult.from_result(result)


@router.post("/{customer_id}/payments", response_model=KhataEntryResult, status_code=201)
def add_payment(customer_id: int, payload: PaymentIn, ctx: Ctx) -> KhataEntryResult:
    """The customer pays. Paying more than is owed leaves an advance (a negative balance)."""
    with write_transaction() as session:
        result = khata_service.record_payment(
            session,
            ctx,
            customer_id,
            payload.amount,
            entry_date=payload.entry_date,
            payment_method=payload.payment_method,
            payment_reference=payload.payment_reference,
            note=payload.note,
        )
        return KhataEntryResult.from_result(result)


@router.post("/{customer_id}/adjustments", response_model=KhataEntryResult, status_code=201)
def add_adjustment(customer_id: int, payload: AdjustmentIn, ctx: Ctx) -> KhataEntryResult:
    """A correction that needs a reason: INCREASE means the customer owes more, DECREASE less."""
    signed = payload.amount if payload.direction is AdjustmentDirection.INCREASE else -payload.amount
    with write_transaction() as session:
        result = khata_service.record_adjustment(
            session, ctx, customer_id, signed, reason=payload.reason, entry_date=payload.entry_date
        )
        return KhataEntryResult.from_result(result)


@router.post("/{customer_id}/ledger/{entry_id}/reverse", response_model=KhataEntryResult, status_code=201)
def reverse_entry(customer_id: int, entry_id: int, payload: ReverseIn, ctx: Ctx) -> KhataEntryResult:
    """Undo an entry with an equal and opposite one. The original is kept. Entries that came from a sale or a
    return cannot be reversed here."""
    with write_transaction() as session:
        result = khata_service.reverse_entry(
            session, ctx, entry_id, reason=payload.reason, customer_id=customer_id
        )
        return KhataEntryResult.from_result(result)
