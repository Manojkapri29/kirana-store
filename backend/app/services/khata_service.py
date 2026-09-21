"""khata_service: the ONLY module allowed to read or write `customer_ledger`.

Same design as `inventory_service`: a customer's balance is derived from an insert-only ledger, so every rule
about it lives here where it can be tested thoroughly. `tests/test_architecture.py` fails if any other module
touches the ledger table.

The ledger (docs/BUSINESS_RULES.md, KH):
  * `amount_delta` is signed. **Positive = the customer owes the shop more; negative = the customer owes
    less.**
  * **Outstanding balance = SUM(amount_delta)** over the customer's entries. Nothing else is the source of
    truth, and no balance is stored on the customer.
  * Types and signs: OPENING_BALANCE (+, an amount already owed), CREDIT_SALE (+), PAYMENT (-), RETURN_CREDIT
    (-), ADJUSTMENT (+ or -, a reason is required), REVERSAL (the exact opposite of the entry it undoes).
  * A negative balance is an **advance**: money the shop holds for the customer. It is never capped or
    discarded, and later credit sales use it up automatically because it is all one sum.
  * Entries are never edited or deleted (database triggers enforce it). A mistake is corrected by a REVERSAL
    that references the original, or by an ADJUSTMENT. An entry can be reversed once, and a reversal cannot
    itself be reversed.

Money is `Decimal` with two places (integer paise in the database). Nothing here uses floats.

Callers: the customer screens use the payment, opening-balance, adjustment and reversal operations. Detailed
Sales (Phase 7) will call `record_credit_sale`, and Sales Returns (Phase 9) `record_return_credit`. Those
workflows do not exist yet; this module only provides the ledger capability, and tests call it directly.

Transaction rule: nothing here commits. The caller opens `write_transaction()`, so an entry and everything
else in the same business action succeed or fail together. The customer's row is locked first so two
operations on one customer line up (PostgreSQL; on SQLite the single writer already serialises).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.context import RequestContext
from app.models import Customer, CustomerLedgerEntry, Sale, User
from app.models.enums import CustomerLedgerEntryType, KhataReferenceType, PaymentMethod
from app.services import customer_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("9999999999.99")  # far above any real khata, well inside the column
MAX_NOTE_LENGTH = 500
MAX_REFERENCE_LENGTH = 100

E = CustomerLedgerEntryType

# Entries that come from a sale document. A person cannot reverse them by hand: the document (a voided sale
# or a cancelled return) has to do it, so the two never disagree.
DOCUMENT_ENTRY_TYPES = frozenset({E.CREDIT_SALE, E.RETURN_CREDIT})


class BalanceStatus(StrEnum):
    OUTSTANDING = "OUTSTANDING"  # the customer owes the shop
    SETTLED = "SETTLED"  # nothing owed either way
    ADVANCE = "ADVANCE"  # the shop holds the customer's money (a negative balance)


def balance_status(balance: Decimal) -> BalanceStatus:
    if balance > 0:
        return BalanceStatus.OUTSTANDING
    if balance < 0:
        return BalanceStatus.ADVANCE
    return BalanceStatus.SETTLED


@dataclass(frozen=True)
class CustomerAccount:
    """A customer together with what the ledger says they owe."""

    customer: Customer
    balance: Decimal  # signed: positive = owes, negative = advance
    entry_count: int

    @property
    def status(self) -> BalanceStatus:
        return balance_status(self.balance)

    @property
    def outstanding(self) -> Decimal:
        return self.balance if self.balance > 0 else ZERO

    @property
    def advance(self) -> Decimal:
        return -self.balance if self.balance < 0 else ZERO


@dataclass(frozen=True)
class LedgerRow:
    id: int
    customer_id: int
    entry_date: date
    entry_type: CustomerLedgerEntryType
    amount_delta: Decimal
    balance_after: Decimal  # the running balance after this entry, in (entry_date, id) order
    payment_method: PaymentMethod | None
    payment_reference: str | None
    reference_type: KhataReferenceType | None
    reference_id: int | None
    reference_no: str | None  # the sale's invoice number, for entries that came from a sale
    reverses_entry_id: int | None  # set on a REVERSAL: the entry it undoes
    reversed_by_entry_id: int | None  # set on an entry that has been reversed
    note: str | None
    created_by_name: str
    created_at: Any


@dataclass(frozen=True)
class EntryResult:
    """A newly written entry and the customer's balance right after it."""

    entry: LedgerRow
    account: CustomerAccount


# --- Reading balances ----------------------------------------------------------------------------


def get_customer_balance(session: Session, shop_id: int, customer_id: int) -> Decimal:
    """What the customer owes (positive) or has paid in advance (negative). Another shop's customer is 404."""
    customer_service.get_customer(session, shop_id, customer_id)
    return _sum_of(session, shop_id, customer_id)


def _sum_of(session: Session, shop_id: int, customer_id: int) -> Decimal:
    total = session.scalar(
        select(func.sum(CustomerLedgerEntry.amount_delta)).where(
            CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.customer_id == customer_id
        )
    )
    return ZERO if total is None else total


def get_balance_map(session: Session, shop_id: int, customer_ids: Sequence[int]) -> dict[int, Decimal]:
    """Balances for several customers in one query. A customer without entries has balance 0."""
    balances = {customer_id: ZERO for customer_id in customer_ids}
    if not customer_ids:
        return balances
    rows = session.execute(
        select(CustomerLedgerEntry.customer_id, func.sum(CustomerLedgerEntry.amount_delta))
        .where(CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.customer_id.in_(customer_ids))
        .group_by(CustomerLedgerEntry.customer_id)
    )
    for customer_id, total in rows:
        balances[customer_id] = total
    return balances


def get_account(session: Session, shop_id: int, customer_id: int) -> CustomerAccount:
    customer = customer_service.get_customer(session, shop_id, customer_id)
    count = session.scalar(
        select(func.count()).where(
            CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.customer_id == customer_id
        )
    )
    return CustomerAccount(customer, _sum_of(session, shop_id, customer_id), count)


def _balance_expression() -> Any:
    """The customer's balance as a SQL expression (one correlated subquery), for filtering and sorting."""
    total = (
        select(func.sum(CustomerLedgerEntry.amount_delta))
        .where(
            CustomerLedgerEntry.shop_id == Customer.shop_id, CustomerLedgerEntry.customer_id == Customer.id
        )
        .correlate(Customer)
        .scalar_subquery()
    )
    return func.coalesce(total, 0)


def list_accounts(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    active: bool | None = True,
    balance: BalanceStatus | None = None,
    biggest_first: bool = False,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[CustomerAccount], int]:
    """Customers with their balances. Search and the active filter are the customer rules; `balance` keeps
    only those owing, settled or in advance; `biggest_first` puts the largest dues first."""
    conditions: list[ColumnElement[bool]] = [Customer.shop_id == shop_id]
    if q and q.strip():
        conditions.append(customer_service.search_clause(q))
    if active is not None:
        conditions.append(Customer.is_active.is_(active))
    owed = _balance_expression()
    if balance is BalanceStatus.OUTSTANDING:
        conditions.append(owed > 0)
    elif balance is BalanceStatus.ADVANCE:
        conditions.append(owed < 0)
    elif balance is BalanceStatus.SETTLED:
        conditions.append(owed == 0)

    total = session.scalar(select(func.count()).select_from(Customer).where(*conditions))
    order = (
        (owed.desc(), func.lower(Customer.name), Customer.id)
        if biggest_first
        else (
            func.lower(Customer.name),
            Customer.id,
        )
    )
    query = select(Customer, owed).where(*conditions).order_by(*order).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = session.execute(query).all()
    counts = _entry_counts(session, shop_id, [row[0].id for row in rows])
    return [CustomerAccount(row[0], row[1], counts.get(row[0].id, 0)) for row in rows], total


def _entry_counts(session: Session, shop_id: int, customer_ids: Sequence[int]) -> dict[int, int]:
    if not customer_ids:
        return {}
    rows = session.execute(
        select(CustomerLedgerEntry.customer_id, func.count())
        .where(CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.customer_id.in_(customer_ids))
        .group_by(CustomerLedgerEntry.customer_id)
    )
    return {customer_id: count for customer_id, count in rows}


# --- Reading the ledger --------------------------------------------------------------------------


def get_customer_ledger(
    session: Session,
    shop_id: int,
    customer_id: int,
    *,
    entry_type: CustomerLedgerEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    newest_first: bool = True,
    limit: int | None = 50,
    offset: int = 0,
    entry_id: int | None = None,
) -> tuple[list[LedgerRow], int]:
    """A customer's history with a running balance ("what happened, and what was owed after it").

    The running balance is computed over the customer's whole history, in (date, id) order; filters only
    narrow which rows are shown. So the last row in date order always shows the customer's current balance.
    """
    customer_service.get_customer(session, shop_id, customer_id)  # 404 for another shop's customer
    row = CustomerLedgerEntry
    reversal = aliased(CustomerLedgerEntry)
    running = func.sum(row.amount_delta).over(order_by=(row.entry_date, row.id))
    history = (
        select(
            row.id,
            row.customer_id,
            row.entry_date,
            row.entry_type,
            row.amount_delta,
            running.label("balance_after"),
            row.payment_method,
            row.payment_reference,
            row.reference_type,
            row.reference_id,
            Sale.invoice_no.label("reference_no"),
            row.reverses_entry_id,
            reversal.id.label("reversed_by_entry_id"),
            row.note,
            User.full_name.label("created_by_name"),
            row.created_at,
        )
        .join(User, and_(User.shop_id == row.shop_id, User.id == row.created_by))
        .outerjoin(reversal, and_(reversal.shop_id == row.shop_id, reversal.reverses_entry_id == row.id))
        .outerjoin(
            Sale,
            and_(
                row.reference_type == KhataReferenceType.SALE,
                Sale.shop_id == row.shop_id,
                Sale.id == row.reference_id,
            ),
        )
        .where(row.shop_id == shop_id, row.customer_id == customer_id)
    )
    h = history.subquery()

    conditions: list[ColumnElement[bool]] = []
    if entry_type is not None:
        conditions.append(h.c.entry_type == entry_type)
    if date_from is not None:
        conditions.append(h.c.entry_date >= date_from)
    if date_to is not None:
        conditions.append(h.c.entry_date <= date_to)
    if entry_id is not None:
        conditions.append(h.c.id == entry_id)

    total = session.scalar(select(func.count()).select_from(h).where(*conditions))
    order = (h.c.entry_date.desc(), h.c.id.desc()) if newest_first else (h.c.entry_date, h.c.id)
    query = select(h).where(*conditions).order_by(*order).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return [LedgerRow(**r._mapping) for r in session.execute(query)], total


# --- Validation helpers --------------------------------------------------------------------------


def _amount(value: Decimal, *, field: str = "amount") -> Decimal:
    """A positive money amount with at most two decimals."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal | int):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field)
    amount = Decimal(value)
    if not amount.is_finite() or amount <= 0:
        raise InvalidInputError("The amount must be greater than zero.", field=field)
    if amount != amount.quantize(Decimal("0.01")):
        raise InvalidInputError("Use at most 2 decimal places.", field=field)
    if amount > MAX_AMOUNT:
        raise InvalidInputError("This amount is too large.", field=field)
    return amount.quantize(Decimal("0.01"))


def _text(value: str | None, *, limit: int, field: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise InvalidInputError(f"Use at most {limit} characters.", field=field)
    return cleaned or None


def _required_text(value: str | None, *, message: str, field: str) -> str:
    cleaned = _text(value, limit=MAX_NOTE_LENGTH, field=field)
    if cleaned is None:
        raise InvalidInputError(message, field=field)
    return cleaned


def _entry_date(session: Session, shop_id: int, when: date | None) -> date:
    today = shop_today(get_shop(session, shop_id))
    if when is None:
        return today
    if when > today:
        raise InvalidInputError("The date cannot be in the future.", field="entry_date")
    return when


def _lock_customer(
    session: Session, ctx: RequestContext, customer_id: int, *, needs_active: bool = False
) -> Customer:
    customer = customer_service.get_customer(session, ctx.shop_id, customer_id, lock=True)
    if needs_active and not customer.is_active:
        raise ConflictError(
            f"'{customer.name}' is inactive. Activate the customer before adding credit.", field="customer_id"
        )
    return customer


def _document_reference(
    reference_type: KhataReferenceType, reference_id: int, allowed: set[KhataReferenceType]
) -> None:
    if reference_type not in allowed:
        names = ", ".join(sorted(t.value for t in allowed))
        raise InvalidInputError(f"The reference type must be one of: {names}.", field="reference_type")
    if reference_id is None or reference_id <= 0:
        raise InvalidInputError("A reference to the source document is required.", field="reference_id")


def _insert(
    session: Session,
    ctx: RequestContext,
    customer: Customer,
    *,
    entry_type: CustomerLedgerEntryType,
    amount_delta: Decimal,
    entry_date: date,
    note: str | None = None,
    payment_method: PaymentMethod | None = None,
    payment_reference: str | None = None,
    reference_type: KhataReferenceType | None = None,
    reference_id: int | None = None,
    reverses_entry_id: int | None = None,
) -> CustomerLedgerEntry:
    entry = CustomerLedgerEntry(
        shop_id=ctx.shop_id,
        customer_id=customer.id,
        entry_date=entry_date,
        entry_type=entry_type,
        amount_delta=amount_delta,
        payment_method=payment_method,
        payment_reference=payment_reference,
        reference_type=reference_type,
        reference_id=reference_id,
        reverses_entry_id=reverses_entry_id,
        note=note,
        created_by=ctx.user_id,
    )
    session.add(entry)
    try:
        session.flush()
    except IntegrityError as exc:  # a concurrent duplicate that slipped past the checks in the callers
        raise ConflictError("This entry conflicts with one that already exists.") from exc
    record_audit(
        session,
        ctx,
        entity_type="customer",
        entity_id=customer.id,
        action=f"khata_{entry_type.value.lower()}",
        after={
            "entry_id": entry.id,
            "amount_delta": amount_delta,
            "entry_date": entry_date,
            "reverses_entry_id": reverses_entry_id,
        },
    )
    return entry


def _result(
    session: Session, ctx: RequestContext, customer: Customer, entry: CustomerLedgerEntry
) -> EntryResult:
    rows, _ = get_customer_ledger(session, ctx.shop_id, customer.id, entry_id=entry.id, limit=1)
    return EntryResult(rows[0], get_account(session, ctx.shop_id, customer.id))


# --- Writing: manual entries ---------------------------------------------------------------------


def create_opening_balance(
    session: Session,
    ctx: RequestContext,
    customer_id: int,
    amount: Decimal,
    *,
    entry_date: date | None = None,
    note: str | None = None,
) -> EntryResult:
    """Record what the customer already owed when the shop started using the system (a positive amount).

    A customer has at most one *live* opening balance, so it cannot be entered twice by accident. If it was
    wrong, reverse it and enter the right one.
    """
    customer = _lock_customer(session, ctx, customer_id)
    value = _amount(amount)
    when = _entry_date(session, ctx.shop_id, entry_date)
    clean_note = _text(note, limit=MAX_NOTE_LENGTH, field="note")

    reversal = aliased(CustomerLedgerEntry)
    live = session.scalar(
        select(CustomerLedgerEntry.id)
        .outerjoin(
            reversal,
            and_(
                reversal.shop_id == CustomerLedgerEntry.shop_id,
                reversal.reverses_entry_id == CustomerLedgerEntry.id,
            ),
        )
        .where(
            CustomerLedgerEntry.shop_id == ctx.shop_id,
            CustomerLedgerEntry.customer_id == customer.id,
            CustomerLedgerEntry.entry_type == E.OPENING_BALANCE,
            reversal.id.is_(None),  # not reversed, so still counts
        )
        .limit(1)
    )
    if live is not None:
        raise ConflictError(
            "This customer already has an opening balance. To change it, reverse it and enter the right one.",
            field="amount",
        )
    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.OPENING_BALANCE,
        amount_delta=value,
        entry_date=when,
        note=clean_note,
    )
    return _result(session, ctx, customer, entry)


def _is_live(session: Session, shop_id: int, entry_id: int) -> bool:
    """True if nothing has reversed this entry."""
    return (
        session.scalar(
            select(CustomerLedgerEntry.id).where(
                CustomerLedgerEntry.shop_id == shop_id, CustomerLedgerEntry.reverses_entry_id == entry_id
            )
        )
        is None
    )


def record_payment(
    session: Session,
    ctx: RequestContext,
    customer_id: int,
    amount: Decimal,
    *,
    entry_date: date | None = None,
    payment_method: PaymentMethod | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
) -> EntryResult:
    """The customer pays the shop. Reduces what they owe by `amount`.

    Paying more than is owed is allowed: the balance goes negative, which is an **advance** (the extra is
    kept, never discarded). Inactive customers can still pay.
    """
    customer = _lock_customer(session, ctx, customer_id)
    value = _amount(amount)
    when = _entry_date(session, ctx.shop_id, entry_date)
    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.PAYMENT,
        amount_delta=-value,
        entry_date=when,
        payment_method=payment_method,
        payment_reference=_text(payment_reference, limit=MAX_REFERENCE_LENGTH, field="payment_reference"),
        note=_text(note, limit=MAX_NOTE_LENGTH, field="note"),
    )
    return _result(session, ctx, customer, entry)


def record_adjustment(
    session: Session,
    ctx: RequestContext,
    customer_id: int,
    amount_delta: Decimal,
    *,
    reason: str | None,
    entry_date: date | None = None,
) -> EntryResult:
    """A controlled correction: `amount_delta` positive means the customer owes more, negative means less.
    A reason is mandatory, and the entry is permanent like every other."""
    customer = _lock_customer(session, ctx, customer_id)
    if (
        isinstance(amount_delta, float)
        or isinstance(amount_delta, bool)
        or not isinstance(amount_delta, Decimal | int)
    ):
        raise InvalidInputError("Enter the amount as a number.", field="amount")
    signed = Decimal(amount_delta)
    if signed == 0:
        raise InvalidInputError("The adjustment cannot be zero.", field="amount")
    magnitude = _amount(abs(signed))
    note = _required_text(reason, message="Give a reason for the adjustment.", field="reason")
    when = _entry_date(session, ctx.shop_id, entry_date)
    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.ADJUSTMENT,
        amount_delta=magnitude if signed > 0 else -magnitude,
        entry_date=when,
        note=note,
    )
    return _result(session, ctx, customer, entry)


# --- Writing: entries that come from documents (called by later phases) ---------------------------


def _find_by_reference(
    session: Session,
    shop_id: int,
    entry_type: CustomerLedgerEntryType,
    reference_type: KhataReferenceType,
    reference_id: int,
) -> CustomerLedgerEntry | None:
    return session.scalar(
        select(CustomerLedgerEntry).where(
            CustomerLedgerEntry.shop_id == shop_id,
            CustomerLedgerEntry.entry_type == entry_type,
            CustomerLedgerEntry.reference_type == reference_type,
            CustomerLedgerEntry.reference_id == reference_id,
        )
    )


def record_credit_sale(
    session: Session,
    ctx: RequestContext,
    customer_id: int,
    amount: Decimal,
    *,
    reference_type: KhataReferenceType,
    reference_id: int,
    entry_date: date | None = None,
    note: str | None = None,
) -> EntryResult:
    """Goods sold on credit: the customer owes `amount` more. **For Detailed Sales (Phase 7) to call**, with
    the sale as the reference. The same document can be recorded only once (a repeat is a conflict).
    The customer must be active. This does not create a sale, and nothing in Phase 6 calls it."""
    customer = _lock_customer(session, ctx, customer_id, needs_active=True)
    _document_reference(
        reference_type, reference_id, {KhataReferenceType.SALE, KhataReferenceType.QUICK_SALE}
    )
    value = _amount(amount)
    when = _entry_date(session, ctx.shop_id, entry_date)
    if _find_by_reference(session, ctx.shop_id, E.CREDIT_SALE, reference_type, reference_id) is not None:
        raise ConflictError("This sale has already been added to a customer's khata.", field="reference_id")
    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.CREDIT_SALE,
        amount_delta=value,
        entry_date=when,
        note=_text(note, limit=MAX_NOTE_LENGTH, field="note"),
        reference_type=reference_type,
        reference_id=reference_id,
    )
    return _result(session, ctx, customer, entry)


def record_return_credit(
    session: Session,
    ctx: RequestContext,
    customer_id: int,
    amount: Decimal,
    *,
    reference_type: KhataReferenceType = KhataReferenceType.SALES_RETURN,
    reference_id: int,
    entry_date: date | None = None,
    note: str | None = None,
) -> EntryResult:
    """Goods returned and credited to the customer's khata: they owe `amount` less. **For Sales Returns
    (Phase 9) to call.** Can push the balance below zero (an advance). Once per document."""
    customer = _lock_customer(session, ctx, customer_id)
    _document_reference(reference_type, reference_id, {KhataReferenceType.SALES_RETURN})
    value = _amount(amount)
    when = _entry_date(session, ctx.shop_id, entry_date)
    if _find_by_reference(session, ctx.shop_id, E.RETURN_CREDIT, reference_type, reference_id) is not None:
        raise ConflictError(
            "This return has already been credited to a customer's khata.", field="reference_id"
        )
    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.RETURN_CREDIT,
        amount_delta=-value,
        entry_date=when,
        note=_text(note, limit=MAX_NOTE_LENGTH, field="note"),
        reference_type=reference_type,
        reference_id=reference_id,
    )
    return _result(session, ctx, customer, entry)


# --- Writing: reversal ---------------------------------------------------------------------------


def reverse_entry(
    session: Session,
    ctx: RequestContext,
    entry_id: int,
    *,
    reason: str | None,
    customer_id: int | None = None,
    allow_document_entries: bool = False,
) -> EntryResult:
    """Undo an entry by adding its exact opposite, linked to it. The original is never touched.

    Refused when: the entry is not in this shop, or not the given customer's (404); it has already been
    reversed; it is itself a reversal.
    Entries that came from a sale or a return (CREDIT_SALE, RETURN_CREDIT) can only be reversed with
    `allow_document_entries=True`, which is for the document workflows of later phases; the customer screens
    never pass it, so a person cannot make the khata disagree with the sale it came from.
    """
    original = session.scalar(
        select(CustomerLedgerEntry).where(
            CustomerLedgerEntry.shop_id == ctx.shop_id, CustomerLedgerEntry.id == entry_id
        )
    )
    if original is None or (customer_id is not None and original.customer_id != customer_id):
        raise NotFoundError("Khata entry not found")  # also the answer for another shop's entry
    customer = _lock_customer(session, ctx, original.customer_id)
    note = _required_text(reason, message="Give a reason for the reversal.", field="reason")

    if original.entry_type is E.REVERSAL:
        raise ConflictError(
            "A reversal cannot be reversed. If it was a mistake, add the entry again or make an adjustment."
        )
    if original.entry_type in DOCUMENT_ENTRY_TYPES and not allow_document_entries:
        raise ConflictError(
            "This entry came from a sale or a return. Cancel that document instead; do not reverse it here."
        )
    if not _is_live(session, ctx.shop_id, original.id):
        raise ConflictError("This entry has already been reversed.")

    entry = _insert(
        session,
        ctx,
        customer,
        entry_type=E.REVERSAL,
        amount_delta=-original.amount_delta,
        entry_date=_entry_date(session, ctx.shop_id, None),
        note=note,
        reverses_entry_id=original.id,
    )
    return _result(session, ctx, customer, entry)


def reverse_credit_sale(
    session: Session,
    ctx: RequestContext,
    reference_type: KhataReferenceType,
    reference_id: int,
    *,
    reason: str,
) -> EntryResult | None:
    """Undo the credit a sale put on a customer's khata (used when the sale is voided).

    Returns `None` when the sale never charged the khata (it was paid in full). This is the document-workflow
    path that is allowed to reverse a CREDIT_SALE entry.
    """
    entry = _find_by_reference(session, ctx.shop_id, E.CREDIT_SALE, reference_type, reference_id)
    if entry is None:
        return None
    return reverse_entry(session, ctx, entry.id, reason=reason, allow_document_entries=True)


# --- Writing: a customer with an opening balance -------------------------------------------------


def create_customer_with_opening_balance(
    session: Session,
    ctx: RequestContext,
    data: dict[str, Any],
    *,
    opening_balance: Decimal | None = None,
    opening_date: date | None = None,
) -> tuple[customer_service.SaveResult, EntryResult | None]:
    """Create a customer and, if given, their opening balance, in the caller's single transaction."""
    saved = customer_service.create_customer(session, ctx, data)
    opening = None
    if opening_balance is not None:
        opening = create_opening_balance(
            session, ctx, saved.customer.id, opening_balance, entry_date=opening_date
        )
    return saved, opening
