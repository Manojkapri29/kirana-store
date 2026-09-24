"""Expenses: DRAFT -> SUBMITTED -> APPROVED -> POSTED, with REJECTED and VOIDED.

Only a POSTED expense reaches the financial reports; posting writes ONE `FinanceEntry` (idempotent on the
expense id) and a void writes its reversal, so nothing is ever deleted or edited in place. Whether a submitted
expense needs a second person's approval is the shop's configurable `expense_approval_threshold`: at or above
it the expense waits in the existing approval queue (the submitter can never approve it); below it, or with no
threshold set, submitting approves it automatically. Categories are the shop's own, never a fixed list.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ApprovalRequest, Expense, ExpenseCategory, FinanceEntry
from app.models.enums import (
    ApprovalStatus,
    CashFlowClass,
    ExpenseStatus,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
)
from app.services import approval_service, finance_ledger_service, numbering_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.finance_settings_service import get_settings

APPROVAL_KIND = "EXPENSE_APPROVAL"
DOC_TYPE = "EXPENSE"
NUMBER_PREFIX = "EXP"
EDITABLE = (ExpenseStatus.DRAFT, ExpenseStatus.REJECTED)


# --- Categories ---------------------------------------------------------------------------------------------


def _clean(value: str | None, *, limit: int, field: str, required: bool = False) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise InvalidInputError("This is required.", field=field)
        return None
    if len(text) > limit:
        raise InvalidInputError(f"Use at most {limit} characters.", field=field)
    return text


def list_categories(session: Session, shop_id: int, *, active_only: bool = False) -> list[ExpenseCategory]:
    query = select(ExpenseCategory).where(ExpenseCategory.shop_id == shop_id).order_by(ExpenseCategory.name)
    if active_only:
        query = query.where(ExpenseCategory.is_active.is_(True))
    return list(session.scalars(query))


def get_category(session: Session, shop_id: int, category_id: int) -> ExpenseCategory:
    category = session.scalar(
        select(ExpenseCategory).where(ExpenseCategory.shop_id == shop_id, ExpenseCategory.id == category_id)
    )
    if category is None:
        raise NotFoundError("Expense category not found")
    return category


def create_category(
    session: Session, ctx: RequestContext, *, name: str, cash_flow_class: CashFlowClass | None = None
) -> ExpenseCategory:
    name = _clean(name, limit=100, field="name", required=True) or ""
    duplicate = session.scalar(
        select(ExpenseCategory).where(
            ExpenseCategory.shop_id == ctx.shop_id, func.lower(ExpenseCategory.name) == name.lower()
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "A category with this name already exists.", field="name", code="duplicate_record"
        )
    category = ExpenseCategory(shop_id=ctx.shop_id, name=name, cash_flow_class=cash_flow_class)
    session.add(category)
    session.flush()
    record_audit(
        session, ctx, entity_type="expense_category", entity_id=category.id, action="expense_category_created",
        after={"name": name, "cash_flow_class": cash_flow_class},
    )  # fmt: skip
    return category


def update_category(
    session: Session, ctx: RequestContext, category_id: int, changes: dict[str, object]
) -> ExpenseCategory:
    """Rename, (de)activate or reclassify. A category is never deleted: deactivating hides it from new
    expenses while every old expense keeps its category."""
    category = get_category(session, ctx.shop_id, category_id)
    before = {
        "name": category.name,
        "is_active": category.is_active,
        "cash_flow_class": category.cash_flow_class,
    }
    if "name" in changes and changes["name"] is not None:
        name = _clean(str(changes["name"]), limit=100, field="name", required=True) or ""
        clash = session.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.shop_id == ctx.shop_id,
                func.lower(ExpenseCategory.name) == name.lower(),
                ExpenseCategory.id != category.id,
            )
        )
        if clash is not None:
            raise ConflictError("A category with this name already exists.", field="name")
        category.name = name
    if "is_active" in changes and changes["is_active"] is not None:
        category.is_active = bool(changes["is_active"])
    if "cash_flow_class" in changes:
        category.cash_flow_class = changes["cash_flow_class"]  # type: ignore[assignment]
    session.flush()
    record_audit(
        session, ctx, entity_type="expense_category", entity_id=category.id, action="expense_category_changed",
        before=before,
        after={"name": category.name, "is_active": category.is_active, "cash_flow_class": category.cash_flow_class},
    )  # fmt: skip
    return category


# --- Expenses -----------------------------------------------------------------------------------------------


def get_expense(session: Session, shop_id: int, expense_id: int, *, lock: bool = False) -> Expense:
    query = select(Expense).where(Expense.shop_id == shop_id, Expense.id == expense_id)
    expense = session.scalar(query.with_for_update() if lock else query)
    if expense is None:
        raise NotFoundError("Expense not found")
    return expense


def list_expenses(
    session: Session, shop_id: int, *, status: ExpenseStatus | None = None, category_id: int | None = None,
    date_from: date | None = None, date_to: date | None = None, q: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[Expense], int]:  # fmt: skip
    conditions = [Expense.shop_id == shop_id]
    if status is not None:
        conditions.append(Expense.status == status)
    if category_id is not None:
        conditions.append(Expense.category_id == category_id)
    if date_from is not None:
        conditions.append(Expense.expense_date >= date_from)
    if date_to is not None:
        conditions.append(Expense.expense_date <= date_to)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        conditions.append(
            func.lower(func.coalesce(Expense.payee, "")).like(like)
            | func.lower(func.coalesce(Expense.description, "")).like(like)
            | func.lower(Expense.expense_no).like(like)
        )
    total = session.scalar(select(func.count()).select_from(Expense).where(*conditions)) or 0
    rows = session.scalars(
        select(Expense).where(*conditions).order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(limit).offset(offset)
    )  # fmt: skip
    return list(rows), total


def _fields(
    session: Session, shop_id: int, data: dict[str, object], *, partial: bool
) -> dict[str, object]:  # fmt: skip
    out: dict[str, object] = {}
    if "amount" in data or not partial:
        out["amount"] = finance_ledger_service.money(data.get("amount"))
    if "expense_date" in data or not partial:
        if not isinstance(data.get("expense_date"), date):
            raise InvalidInputError("Enter the date of the expense.", field="expense_date")
        out["expense_date"] = data["expense_date"]
    if "category_id" in data or not partial:
        category = get_category(session, shop_id, int(data.get("category_id") or 0))
        if not category.is_active:
            raise InvalidInputError("This category is no longer in use.", field="category_id")
        out["category_id"] = category.id
    if "payment_method" in data or not partial:
        try:
            out["payment_method"] = FinancePaymentMethod(data.get("payment_method"))
        except ValueError as exc:
            raise InvalidInputError("Choose how it was paid.", field="payment_method") from exc
    for name, limit in (("payee", 150), ("description", 500), ("attachment_ref", 300), ("notes", 4000)):
        if name in data:
            out[name] = _clean(data[name], limit=limit, field=name)  # type: ignore[arg-type]
    return out


def create_expense(session: Session, ctx: RequestContext, data: dict[str, object]) -> Expense:
    fields = _fields(session, ctx.shop_id, data, partial=False)
    year = numbering_service.fiscal_year_label(fields["expense_date"])  # type: ignore[arg-type]
    number = numbering_service.next_number(session, ctx.shop_id, DOC_TYPE, year)
    expense = Expense(
        shop_id=ctx.shop_id, created_by=ctx.user_id, status=ExpenseStatus.DRAFT,
        expense_no=numbering_service.format_document_number(NUMBER_PREFIX, year, number), **fields,
    )  # fmt: skip
    session.add(expense)
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id, action="expense_created",
        after={"expense_no": expense.expense_no, "amount": expense.amount, "expense_date": expense.expense_date},
    )  # fmt: skip
    return expense


def update_expense(
    session: Session, ctx: RequestContext, expense_id: int, data: dict[str, object]
) -> Expense:
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status not in EDITABLE:
        raise ConflictError("Only a draft or rejected expense can be edited.", code="not_editable")
    before = {"amount": expense.amount, "expense_date": expense.expense_date, "status": expense.status}
    for name, value in _fields(session, ctx.shop_id, data, partial=True).items():
        setattr(expense, name, value)
    expense.status = ExpenseStatus.DRAFT  # editing a rejected expense sends it back to draft
    expense.rejection_reason = None
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id, action="expense_edited", before=before,
        after={"amount": expense.amount, "expense_date": expense.expense_date},
    )  # fmt: skip
    return expense


def _pending_approval(session: Session, shop_id: int, expense_id: int) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.shop_id == shop_id,
            ApprovalRequest.kind == APPROVAL_KIND,
            ApprovalRequest.entity_type == "expense",
            ApprovalRequest.entity_id == expense_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
        .limit(1)
    )


def submit_expense(session: Session, ctx: RequestContext, expense_id: int) -> Expense:
    """DRAFT -> SUBMITTED, then straight on to APPROVED unless the shop's approval limit applies."""
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status is not ExpenseStatus.DRAFT:
        raise ConflictError("Only a draft expense can be submitted.", code="not_draft")
    threshold = get_settings(session, ctx.shop_id).expense_approval_threshold
    expense.submitted_at = utc_now()
    if threshold is not None and expense.amount >= threshold:
        expense.status = ExpenseStatus.SUBMITTED
        expense.requires_approval = True
        approval_service.create(
            session, ctx, kind=APPROVAL_KIND, entity_type="expense", entity_id=expense.id,
            reason=f"Expense {expense.expense_no} of {expense.amount} reaches the approval limit of {threshold}.",
            threshold_value=threshold, observed_value=expense.amount,
        )  # fmt: skip
    else:
        expense.status = ExpenseStatus.APPROVED
        expense.requires_approval = False
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id, action="expense_submitted",
        after={"status": expense.status, "requires_approval": expense.requires_approval},
    )  # fmt: skip
    return expense


def apply_approval_decision(
    session: Session, ctx: RequestContext, expense_id: int, *, approved: bool, note: str | None = None
) -> Expense:  # fmt: skip
    """Called once the approval request for this expense has been decided by someone other than its submitter."""
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status is not ExpenseStatus.SUBMITTED:
        raise ConflictError("This expense is not waiting for approval.", code="not_submitted")
    if approved:
        expense.status = ExpenseStatus.APPROVED
        expense.approved_by = ctx.user_id
        expense.approved_at = utc_now()
    else:
        expense.status = ExpenseStatus.REJECTED
        expense.rejection_reason = (note or "").strip() or "Rejected"
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id,
        action="expense_approved" if approved else "expense_rejected", after={"note": note},
    )  # fmt: skip
    return expense


def decide(
    session: Session, ctx: RequestContext, expense_id: int, *, approve: bool, note: str | None = None
) -> Expense:  # fmt: skip
    """Approve or reject a SUBMITTED expense: finds its pending approval request and decides it through the
    shared approval queue (no self-approval, a rejection needs a reason), then applies the outcome."""
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status is not ExpenseStatus.SUBMITTED:
        raise ConflictError("This expense is not waiting for approval.", code="not_submitted")
    request = _pending_approval(session, ctx.shop_id, expense.id)
    if request is None:
        raise ConflictError("There is no pending approval request for this expense.")
    approval_service.decide(session, ctx, request.id, approve=approve, note=note)
    return apply_approval_decision(session, ctx, expense.id, approved=approve, note=note)


def post_expense(session: Session, ctx: RequestContext, expense_id: int) -> tuple[Expense, bool]:
    """APPROVED -> POSTED. Writes the expense's ledger entry exactly once: posting an expense that is already
    posted changes nothing and reports `created=False`."""
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status is ExpenseStatus.POSTED:
        return expense, False
    if expense.status is not ExpenseStatus.APPROVED:
        raise ConflictError("Only an approved expense can be posted.", code="not_approved")
    category = get_category(session, ctx.shop_id, expense.category_id)
    entry, created = finance_ledger_service.record_entry(
        session, ctx, event_type=FinanceEventType.EXPENSE, direction=FlowDirection.OUT, amount=expense.amount,
        payment_method=expense.payment_method, entry_date=expense.expense_date, reference_type="EXPENSE",
        reference_id=expense.id, note=expense.expense_no, cash_flow_class=category.cash_flow_class,
        action="expense_post",
    )  # fmt: skip
    expense.status = ExpenseStatus.POSTED
    expense.posted_by = ctx.user_id
    expense.posted_at = utc_now()
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id, action="expense_posted",
        after={"entry_id": entry.id, "amount": expense.amount},
    )  # fmt: skip
    return expense, created


def void_expense(
    session: Session, ctx: RequestContext, expense_id: int, reason: str, *, today: date
) -> Expense:  # fmt: skip
    """POSTED -> VOIDED by writing the reversing ledger entry. Voiding twice does nothing more."""
    if not reason.strip():
        raise InvalidInputError("Give a reason for voiding this expense.", field="reason")
    expense = get_expense(session, ctx.shop_id, expense_id, lock=True)
    if expense.status is ExpenseStatus.VOIDED:
        return expense
    if expense.status is not ExpenseStatus.POSTED:
        raise ConflictError("Only a posted expense can be voided.", code="not_posted")
    entry = finance_ledger_service.find_entry(
        session, ctx.shop_id, FinanceEventType.EXPENSE, "EXPENSE", expense.id
    )
    if entry is None:  # cannot happen through the API: a posted expense always has its entry
        raise ConflictError("The ledger entry for this expense is missing.")
    finance_ledger_service.reverse_entry(session, ctx, entry.id, reason, today=today)
    expense.status = ExpenseStatus.VOIDED
    expense.void_reason = reason.strip()
    expense.voided_at = utc_now()
    session.flush()
    record_audit(
        session, ctx, entity_type="expense", entity_id=expense.id, action="expense_voided",
        after={"reason": reason.strip()},
    )  # fmt: skip
    return expense


# --- Reporting helpers --------------------------------------------------------------------------------------


def expenses_by_category(
    session: Session, shop_id: int, start: date, end: date
) -> list[tuple[int, str, Decimal]]:  # fmt: skip
    """Net posted expense per category for `start`..`end`: posted entries less their reversals, by entry date.
    Returned largest first."""
    entries = list(
        session.scalars(
            select(FinanceEntry).where(
                FinanceEntry.shop_id == shop_id,
                FinanceEntry.event_type == FinanceEventType.EXPENSE,
                FinanceEntry.entry_date >= start,
                FinanceEntry.entry_date <= end,
            )
        )
    )
    if not entries:
        return []
    originals = {e.id: e for e in entries if e.reverses_entry_id is None}
    missing = {
        e.reverses_entry_id for e in entries if e.reverses_entry_id and e.reverses_entry_id not in originals
    }
    if missing:
        for e in session.scalars(
            select(FinanceEntry).where(FinanceEntry.shop_id == shop_id, FinanceEntry.id.in_(missing))
        ):
            originals[e.id] = e
    expense_of = {
        e.id: e.reference_id for e in originals.values() if e.reference_type == "EXPENSE" and e.reference_id
    }
    category_of = dict(
        session.execute(
            select(Expense.id, Expense.category_id).where(
                Expense.shop_id == shop_id, Expense.id.in_(set(expense_of.values()))
            )
        ).all()
    )
    totals: dict[int, Decimal] = {}
    for e in entries:
        original_id = e.reverses_entry_id or e.id
        category_id = category_of.get(expense_of.get(original_id, -1))
        if category_id is None:
            continue
        signed = e.amount if e.direction is FlowDirection.OUT else -e.amount
        totals[category_id] = totals.get(category_id, Decimal("0.00")) + signed
    names = {c.id: c.name for c in list_categories(session, shop_id)}
    rows = [(cid, names.get(cid, "?"), total) for cid, total in totals.items() if total != 0]
    return sorted(rows, key=lambda r: (-r[2], r[1]))


def posted_expense_total(session: Session, shop_id: int, start: date, end: date) -> Decimal:
    """Net posted expense for the period: posted less reversed. Drafts, submitted, rejected: never included."""
    total = Decimal("0.00")
    for e in session.scalars(
        select(FinanceEntry).where(
            FinanceEntry.shop_id == shop_id, FinanceEntry.event_type == FinanceEventType.EXPENSE,
            FinanceEntry.entry_date >= start, FinanceEntry.entry_date <= end,
        )
    ):  # fmt: skip
        total += e.amount if e.direction is FlowDirection.OUT else -e.amount
    return total
