"""Customers: the people the shop sells to, mostly on credit.

Generic for every kind of business: nothing here depends on the business type.

This module manages the customer *record* only (name, contact details, active flag). It knows nothing about
money: a customer's balance is derived from the khata ledger, which only `khata_service` may read or write.
Screens that need a customer together with their balance go through `khata_service`.

Rules (see docs/BUSINESS_RULES.md, KH1 to KH3):
  * only the name is required; phone, email, address and notes are optional;
  * phone and email are validated leniently and stored in a consistent form (`contact_validation`);
  * a phone number is unique **within the shop** (never across shops: two shops may share a customer's
    number), and the message names the customer who already has it. Names are not unique: two real
    customers can share a name, so a repeated name produces a warning and is saved;
  * customers are never deleted, only deactivated: their ledger must stay explainable. An inactive customer is
    hidden from default lists and cannot receive new credit, but can still pay, and can be reactivated;
  * every query is scoped to the caller's shop; another shop's customer is simply "not found".
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import Customer
from app.services.audit_service import record_audit
from app.services.catalog_service import clean_name
from app.services.contact_validation import blank_to_none, normalize_email, normalize_phone
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

UPDATABLE_FIELDS = {"name", "phone", "email", "address", "notes"}
AUDITED_FIELDS = (*sorted(UPDATABLE_FIELDS), "is_active")


@dataclass
class SaveResult:
    customer: Customer
    warnings: list[str] = field(default_factory=list)


def get_customer(session: Session, shop_id: int, customer_id: int, *, lock: bool = False) -> Customer:
    """Load a customer of this shop. `lock` holds its row for the rest of the transaction (PostgreSQL;
    SQLite's single writer already serialises), which is how balance-changing operations line up."""
    query = select(Customer).where(Customer.shop_id == shop_id, Customer.id == customer_id)
    customer = session.scalar(query.with_for_update() if lock else query)
    if customer is None:
        raise NotFoundError("Customer not found")  # also the answer for another shop's customer
    return customer


def search_clause(text: str) -> ColumnElement[bool]:
    """Match name, email, address or a phone number (typed with or without spaces and dashes)."""
    needle = text.strip().lower()
    clauses = [
        func.lower(Customer.name).contains(needle, autoescape=True),
        func.lower(func.coalesce(Customer.email, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Customer.address, "")).contains(needle, autoescape=True),
    ]
    digits = "".join(ch for ch in needle if ch.isdigit() or ch == "+")
    if len(digits) >= 3:  # phone numbers are stored compactly, so compare compactly
        clauses.append(func.coalesce(Customer.phone, "").contains(digits, autoescape=True))
    return or_(*clauses)


def list_customers(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    active: bool | None = True,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[Customer], int]:
    """Customers by name. (The list with balances, and balance filters, live in `khata_service`.)"""
    conditions: list[ColumnElement[bool]] = [Customer.shop_id == shop_id]
    if q and q.strip():
        conditions.append(search_clause(q))
    if active is not None:
        conditions.append(Customer.is_active.is_(active))
    total = session.scalar(select(func.count()).select_from(Customer).where(*conditions))
    query = (
        select(Customer).where(*conditions).order_by(func.lower(Customer.name), Customer.id).offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query)), total


# --- Validation ----------------------------------------------------------------------------------


def _normalize(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean the fields present in `changes`. Blank optional fields become NULL.

    Every invalid field is reported at once, so a form can mark them all together.
    """
    out = dict(changes)
    problems: list[tuple[str | None, str]] = []

    def clean(key: str, fix: Any) -> None:
        if key not in out:
            return
        try:
            out[key] = fix(out[key])
        except InvalidInputError as exc:
            problems.append((exc.field, exc.message))

    def name(value: str | None) -> str:
        cleaned = clean_name(value or "")
        if not cleaned:
            raise InvalidInputError("Enter the customer's name.", field="name")
        return cleaned

    clean("name", name)
    clean("phone", lambda v: normalize_phone(v, field="phone"))
    clean("email", normalize_email)
    clean("address", blank_to_none)
    clean("notes", blank_to_none)

    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


def _check_phone_free(session: Session, shop_id: int, phone: str | None, *, exclude_id: int | None) -> None:
    if phone is None:
        return
    query = select(Customer.name).where(Customer.shop_id == shop_id, Customer.phone == phone)
    if exclude_id is not None:
        query = query.where(Customer.id != exclude_id)
    owner = session.scalar(query.limit(1))
    if owner is not None:
        raise ConflictError(f"The phone number {phone} already belongs to '{owner}'.", field="phone")


def _name_warnings(session: Session, shop_id: int, name: str | None, *, exclude_id: int | None) -> list[str]:
    if not name:
        return []
    query = select(Customer.name).where(
        Customer.shop_id == shop_id, func.lower(Customer.name) == name.lower()
    )
    if exclude_id is not None:
        query = query.where(Customer.id != exclude_id)
    same = session.scalar(query.limit(1))
    return [] if same is None else [f"Another customer is also named '{same}'."]


def _snapshot(customer: Customer) -> dict[str, Any]:
    return {name: getattr(customer, name) for name in AUDITED_FIELDS}


def _flush_or_conflict(session: Session) -> None:
    """The database's unique phone rule is the backstop for two requests racing past the check above."""
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError("A customer with this phone number already exists.", field="phone") from exc


# --- Writing -------------------------------------------------------------------------------------


def create_customer(session: Session, ctx: RequestContext, data: dict[str, Any]) -> SaveResult:
    data = {key: data.get(key) for key in UPDATABLE_FIELDS if key in data}
    data.setdefault("name", None)  # a missing name is reported like a blank one
    values = _normalize(data)
    _check_phone_free(session, ctx.shop_id, values.get("phone"), exclude_id=None)
    warnings = _name_warnings(session, ctx.shop_id, values.get("name"), exclude_id=None)

    customer = Customer(shop_id=ctx.shop_id, **values)
    session.add(customer)
    _flush_or_conflict(session)
    session.refresh(customer)
    record_audit(
        session,
        ctx,
        entity_type="customer",
        entity_id=customer.id,
        action="create",
        after=_snapshot(customer),
    )
    return SaveResult(customer, warnings)


def update_customer(
    session: Session, ctx: RequestContext, customer_id: int, changes: dict[str, Any]
) -> SaveResult:
    """Change some details. Only fields present in `changes` are touched; `null` clears an optional field."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidInputError(f"These fields cannot be changed: {', '.join(sorted(unknown))}.")
    if "name" in changes and changes["name"] is None:
        raise InvalidInputError("Enter the customer's name.", field="name")

    customer = get_customer(session, ctx.shop_id, customer_id)
    changes = _normalize(changes)
    changes = {key: value for key, value in changes.items() if getattr(customer, key) != value}
    warnings: list[str] = []

    if changes:
        if "phone" in changes:
            _check_phone_free(session, ctx.shop_id, changes["phone"], exclude_id=customer.id)
        if "name" in changes:
            warnings = _name_warnings(session, ctx.shop_id, changes["name"], exclude_id=customer.id)
        before = {key: getattr(customer, key) for key in changes}
        for key, value in changes.items():
            setattr(customer, key, value)
        _flush_or_conflict(session)
        session.refresh(customer)
        record_audit(
            session,
            ctx,
            entity_type="customer",
            entity_id=customer.id,
            action="update",
            before=before,
            after={key: getattr(customer, key) for key in changes},
        )
    return SaveResult(customer, warnings)


def set_customer_active(session: Session, ctx: RequestContext, customer_id: int, *, active: bool) -> Customer:
    """Activate or deactivate. The ledger is untouched; repeating a call is harmless."""
    customer = get_customer(session, ctx.shop_id, customer_id)
    if customer.is_active != active:
        customer.is_active = active
        session.flush()
        record_audit(
            session,
            ctx,
            entity_type="customer",
            entity_id=customer.id,
            action="activate" if active else "deactivate",
            before={"is_active": not active},
            after={"is_active": active},
        )
    return customer
