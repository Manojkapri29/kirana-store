"""Finance: settings, expenses, the financial ledger, adjustments and period controls. See the finance services
for every rule; this router parses requests, opens the write transaction and shapes the response."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.idempotency import IdempotencyHeader, run_idempotent
from app.db.session import get_session, write_transaction
from app.models.enums import (
    ExpenseStatus,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
)
from app.schemas.finance import (
    AdjustmentIn,
    ApprovalRequiredOut,
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    DecisionIn,
    EntryIn,
    EntryOut,
    ExpenseIn,
    ExpenseListOut,
    ExpenseOut,
    ExpensePatch,
    LedgerListOut,
    LedgerRowOut,
    PeriodIn,
    PeriodOut,
    ReasonIn,
    SettingsIO,
)
from app.services import (
    expense_service,
    finance_ledger_service,
    finance_period_service,
    finance_settings_service,
)
from app.services.errors import InvalidInputError
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/finance", tags=["finance"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]

# The only event types a person records by hand. Everything else is read from its own document.
MANUAL_EVENTS = (
    FinanceEventType.SUPPLIER_PAYMENT,
    FinanceEventType.OWNER_CAPITAL,
    FinanceEventType.OWNER_WITHDRAWAL,
    FinanceEventType.OTHER_INCOME,
)
_DIRECTION = {
    FinanceEventType.SUPPLIER_PAYMENT: FlowDirection.OUT,
    FinanceEventType.OWNER_CAPITAL: FlowDirection.IN,
    FinanceEventType.OWNER_WITHDRAWAL: FlowDirection.OUT,
    FinanceEventType.OTHER_INCOME: FlowDirection.IN,
}


def _today(session: Session, shop_id: int) -> date:
    return shop_today(get_shop(session, shop_id))


# --- Settings ------------------------------------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsIO)
def get_settings(ctx: Ctx, session: ReadSession) -> SettingsIO:
    return SettingsIO(
        **finance_settings_service.as_dict(finance_settings_service.get_settings(session, ctx.shop_id))
    )


@router.put("/settings", response_model=SettingsIO)
def put_settings(payload: SettingsIO, ctx: Ctx) -> SettingsIO:
    with write_transaction() as session:
        row = finance_settings_service.update_settings(session, ctx, payload.model_dump())
        return SettingsIO(**finance_settings_service.as_dict(row))


# --- Expense categories ----------------------------------------------------------------------------------------------


@router.get("/expense-categories", response_model=list[CategoryOut])
def list_categories(ctx: Ctx, session: ReadSession, active_only: bool = False) -> list[CategoryOut]:
    return [
        CategoryOut.of(c)
        for c in expense_service.list_categories(session, ctx.shop_id, active_only=active_only)
    ]


@router.post("/expense-categories", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryIn, ctx: Ctx) -> CategoryOut:
    with write_transaction() as session:
        return CategoryOut.of(
            expense_service.create_category(
                session, ctx, name=payload.name, cash_flow_class=payload.cash_flow_class
            )
        )


@router.patch("/expense-categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, payload: CategoryPatch, ctx: Ctx) -> CategoryOut:
    with write_transaction() as session:
        return CategoryOut.of(
            expense_service.update_category(session, ctx, category_id, payload.model_dump(exclude_unset=True))
        )


# --- Expenses --------------------------------------------------------------------------------------------------------


@router.get("/expenses", response_model=ExpenseListOut)
def list_expenses(
    ctx: Ctx, session: ReadSession, status: ExpenseStatus | None = None, category_id: int | None = None,
    date_from: date | None = None, date_to: date | None = None, q: str | None = None,
    limit: Limit = 50, offset: Offset = 0,
) -> ExpenseListOut:  # fmt: skip
    rows, total = expense_service.list_expenses(
        session, ctx.shop_id, status=status, category_id=category_id, date_from=date_from, date_to=date_to,
        q=q, limit=limit, offset=offset,
    )  # fmt: skip
    return ExpenseListOut(items=[ExpenseOut.of(e) for e in rows], total=total, limit=limit, offset=offset)


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(payload: ExpenseIn, ctx: Ctx, idempotency_key: IdempotencyHeader = None) -> ExpenseOut:
    """Creates a DRAFT. A draft never affects any report."""
    return run_idempotent(
        ctx, idempotency_key, "expense.create", payload.model_dump(mode="json"),
        lambda session: ExpenseOut.of(expense_service.create_expense(session, ctx, payload.model_dump())),
        status_code=201,
    )  # fmt: skip


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, ctx: Ctx, session: ReadSession) -> ExpenseOut:
    return ExpenseOut.of(expense_service.get_expense(session, ctx.shop_id, expense_id))


@router.patch("/expenses/{expense_id}", response_model=ExpenseOut)
def update_expense(expense_id: int, payload: ExpensePatch, ctx: Ctx) -> ExpenseOut:
    with write_transaction() as session:
        return ExpenseOut.of(
            expense_service.update_expense(session, ctx, expense_id, payload.model_dump(exclude_unset=True))
        )


@router.post("/expenses/{expense_id}/submit", response_model=ExpenseOut)
def submit_expense(expense_id: int, ctx: Ctx) -> ExpenseOut:
    with write_transaction() as session:
        return ExpenseOut.of(expense_service.submit_expense(session, ctx, expense_id))


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseOut)
def approve_expense(expense_id: int, ctx: Ctx, payload: DecisionIn | None = None) -> ExpenseOut:
    with write_transaction() as session:
        note = payload.note if payload else None
        return ExpenseOut.of(expense_service.decide(session, ctx, expense_id, approve=True, note=note))


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseOut)
def reject_expense(expense_id: int, payload: ReasonIn, ctx: Ctx) -> ExpenseOut:
    with write_transaction() as session:
        return ExpenseOut.of(
            expense_service.decide(session, ctx, expense_id, approve=False, note=payload.reason)
        )


@router.post("/expenses/{expense_id}/post", response_model=ExpenseOut)
def post_expense(expense_id: int, ctx: Ctx, idempotency_key: IdempotencyHeader = None) -> ExpenseOut:
    """Posts an approved expense to the ledger. Repeating it, with or without a key, posts nothing twice."""
    return run_idempotent(
        ctx, idempotency_key, "expense.post", {"expense_id": expense_id},
        lambda session: ExpenseOut.of(expense_service.post_expense(session, ctx, expense_id)[0]),
    )  # fmt: skip


@router.post("/expenses/{expense_id}/void", response_model=ExpenseOut)
def void_expense(expense_id: int, payload: ReasonIn, ctx: Ctx) -> ExpenseOut:
    with write_transaction() as session:
        return ExpenseOut.of(
            expense_service.void_expense(
                session, ctx, expense_id, payload.reason, today=_today(session, ctx.shop_id)
            )
        )


# --- Ledger ----------------------------------------------------------------------------------------------------------


@router.get("/ledger", response_model=LedgerListOut)
def get_ledger(
    ctx: Ctx, session: ReadSession, date_from: date | None = None, date_to: date | None = None,
    event_type: Annotated[list[FinanceEventType] | None, Query()] = None, payment_method: str | None = None,
    customer_id: int | None = None, supplier_id: int | None = None, status: str | None = None,
    reference: str | None = None, source_type: str | None = None, limit: Limit = 100, offset: Offset = 0,
) -> LedgerListOut:  # fmt: skip
    today = _today(session, ctx.shop_id)
    end = date_to or today
    start = date_from or end - timedelta(days=30)
    if start > end:
        raise InvalidInputError("The start date is after the end date.", field="date_from")
    rows = finance_ledger_service.list_ledger(
        session, ctx.shop_id, start, end, event_types=event_type, payment_method=payment_method,
        customer_id=customer_id, supplier_id=supplier_id, status=status, reference=reference,
        source_type=source_type,
    )  # fmt: skip
    total_in = sum((r.settled_amount for r in rows if r.direction is FlowDirection.IN), Decimal("0.00"))
    total_out = sum((r.settled_amount for r in rows if r.direction is FlowDirection.OUT), Decimal("0.00"))
    page = rows[offset : offset + limit]
    return LedgerListOut(
        items=[LedgerRowOut.of(r) for r in page], total=len(rows), limit=limit, offset=offset,
        total_in=total_in, total_out=total_out,
    )  # fmt: skip


@router.post("/entries", response_model=EntryOut, status_code=201)
def record_entry(payload: EntryIn, ctx: Ctx, idempotency_key: IdempotencyHeader = None) -> EntryOut:
    """Records a supplier payment, owner capital/withdrawal or other income. Send an Idempotency-Key so a
    repeat never records it twice."""
    if payload.event_type not in MANUAL_EVENTS:
        raise InvalidInputError(
            "Only supplier payments, owner capital, owner withdrawals and other income are recorded here.",
            field="event_type",
        )
    if payload.event_type is FinanceEventType.SUPPLIER_PAYMENT and payload.supplier_id is None:
        raise InvalidInputError("Choose the supplier that was paid.", field="supplier_id")

    def produce(session: Session) -> EntryOut:
        entry, _ = finance_ledger_service.record_entry(
            session, ctx, event_type=payload.event_type, direction=_DIRECTION[payload.event_type],
            amount=payload.amount, payment_method=payload.payment_method, entry_date=payload.entry_date,
            supplier_id=payload.supplier_id, note=payload.note, cash_flow_class=payload.cash_flow_class,
            action="record_entry",
        )  # fmt: skip
        return EntryOut.of(entry)

    return run_idempotent(
        ctx, idempotency_key, "finance.entry", payload.model_dump(mode="json"), produce, status_code=201
    )


@router.post("/entries/{entry_id}/reverse", response_model=EntryOut, status_code=201)
def reverse_entry(entry_id: int, payload: ReasonIn, ctx: Ctx) -> EntryOut:
    with write_transaction() as session:
        mirror, _ = finance_ledger_service.reverse_entry(
            session, ctx, entry_id, payload.reason, today=_today(session, ctx.shop_id)
        )
        return EntryOut.of(mirror)


@router.post("/adjustments", response_model=EntryOut | ApprovalRequiredOut, status_code=201)
def record_adjustment(
    payload: AdjustmentIn, ctx: Ctx, response: Response, idempotency_key: IdempotencyHeader = None
) -> EntryOut | ApprovalRequiredOut:
    """A manual financial or cash adjustment. Above the shop's limit it answers 202 with an approval request id
    and records nothing; once a second person approves, send it again with `approval_request_id`. Send an
    Idempotency-Key so a repeat never records the adjustment twice."""

    def produce(session: Session) -> EntryOut | ApprovalRequiredOut:
        outcome = finance_ledger_service.record_adjustment(
            session, ctx, direction=payload.direction, amount=payload.amount,
            payment_method=payload.payment_method, entry_date=payload.entry_date, note=payload.note,
            approval_request_id=payload.approval_request_id,
        )  # fmt: skip
        if outcome.entry is None:
            return ApprovalRequiredOut(approval_request_id=outcome.approval_request_id or 0)
        return EntryOut.of(outcome.entry)

    result = run_idempotent(
        ctx, idempotency_key, "finance.adjustment", payload.model_dump(mode="json"), produce, status_code=201
    )
    if isinstance(result, ApprovalRequiredOut):
        response.status_code = 202
    return result


# --- Periods ---------------------------------------------------------------------------------------------------------


def _period_out(session: Session, period) -> PeriodOut:
    return PeriodOut.of(period, reopen_pending=finance_period_service.has_pending_reopen(session, period))


@router.get("/periods", response_model=list[PeriodOut])
def list_periods(ctx: Ctx, session: ReadSession) -> list[PeriodOut]:
    return [_period_out(session, p) for p in finance_period_service.list_periods(session, ctx.shop_id)]


@router.post("/periods", response_model=PeriodOut, status_code=201)
def create_period(payload: PeriodIn, ctx: Ctx) -> PeriodOut:
    with write_transaction() as session:
        return _period_out(
            session,
            finance_period_service.create_period(
                session,
                ctx,
                period_start=payload.period_start,
                period_end=payload.period_end,
                note=payload.note,
            ),
        )


@router.post("/periods/{period_id}/lock", response_model=PeriodOut)
def lock_period(period_id: int, ctx: Ctx) -> PeriodOut:
    with write_transaction() as session:
        return _period_out(session, finance_period_service.lock(session, ctx, period_id))


@router.post("/periods/{period_id}/close", response_model=PeriodOut)
def close_period(period_id: int, ctx: Ctx) -> PeriodOut:
    with write_transaction() as session:
        return _period_out(session, finance_period_service.close(session, ctx, period_id))


@router.post("/periods/{period_id}/unlock", response_model=PeriodOut)
def unlock_period(period_id: int, ctx: Ctx) -> PeriodOut:
    with write_transaction() as session:
        return _period_out(session, finance_period_service.unlock(session, ctx, period_id))


@router.post("/periods/{period_id}/reopen", response_model=PeriodOut)
def reopen_period(period_id: int, payload: ReasonIn, ctx: Ctx) -> PeriodOut:
    """Reopens a closed period. When the shop requires approval this opens a request and the period stays closed
    (`reopen_approval_pending: true`) until a second person approves it and this is sent again."""
    with write_transaction() as session:
        return _period_out(session, finance_period_service.reopen(session, ctx, period_id, payload.reason))


__all__ = ["router", "FinancePaymentMethod"]
