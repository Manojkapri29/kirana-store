"""Suppliers: people and businesses the shop buys from.

Generic for every kind of business: nothing here depends on the business type.

Rules (see docs/BUSINESS_RULES.md, SP1 to SP7):
  * only the name is required; phone, alternate phone, email, address, GSTIN and notes are optional;
  * contact details are validated leniently and stored in a consistent form (`contact_validation`);
  * names, phones and GSTINs are NOT unique: two genuine suppliers can share a name, and one person can answer
    two businesses' phones. A likely duplicate produces a *warning* and the supplier is still saved;
  * suppliers are never deleted, only deactivated. An inactive supplier is hidden from default lists and
    pickers and cannot be newly chosen as a product's default supplier, but keeps its history and can be
    reactivated;
  * every query is scoped to the caller's shop; another shop's supplier is simply "not found".

Purchases, purchase returns, payments and a supplier ledger do not exist yet (later phases).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import Product, Supplier
from app.services.audit_service import record_audit
from app.services.catalog_service import clean_name
from app.services.contact_validation import (
    blank_to_none,
    normalize_email,
    normalize_gstin,
    normalize_phone,
)
from app.services.errors import InvalidInputError, NotFoundError

UPDATABLE_FIELDS = {"name", "phone", "alternate_phone", "email", "address", "gstin", "notes"}
AUDITED_FIELDS = (*sorted(UPDATABLE_FIELDS), "is_active")


@dataclass(frozen=True)
class SupplierView:
    supplier: Supplier
    product_count: int  # products that name this supplier as their default supplier


@dataclass
class SaveResult:
    view: SupplierView
    warnings: list[str] = field(default_factory=list)


# --- Reading -------------------------------------------------------------------------------------


def _product_count() -> Any:
    return (
        select(func.count())
        .where(Product.shop_id == Supplier.shop_id, Product.default_supplier_id == Supplier.id)
        .correlate(Supplier)
        .scalar_subquery()
    )


def get_supplier(session: Session, shop_id: int, supplier_id: int) -> Supplier:
    supplier = session.scalar(select(Supplier).where(Supplier.shop_id == shop_id, Supplier.id == supplier_id))
    if supplier is None:
        raise NotFoundError("Supplier not found")  # also the answer for another shop's supplier
    return supplier


def get_supplier_view(session: Session, shop_id: int, supplier_id: int) -> SupplierView:
    row = session.execute(
        select(Supplier, _product_count())
        .where(Supplier.shop_id == shop_id, Supplier.id == supplier_id)
        .execution_options(populate_existing=True)  # show what is stored, not the raw input
    ).first()
    if row is None:
        raise NotFoundError("Supplier not found")
    return SupplierView(supplier=row[0], product_count=row[1])


def _search_clause(text: str) -> ColumnElement[bool]:
    """Match name, email, GSTIN or a phone number (typed with or without spaces/dashes)."""
    needle = text.strip().lower()
    clauses = [
        func.lower(Supplier.name).contains(needle, autoescape=True),
        func.lower(func.coalesce(Supplier.email, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Supplier.gstin, "")).contains(needle, autoescape=True),
    ]
    digits = "".join(ch for ch in needle if ch.isdigit() or ch == "+")
    if len(digits) >= 3:  # phone numbers are stored compact, so compare compact
        clauses.append(func.coalesce(Supplier.phone, "").contains(digits, autoescape=True))
        clauses.append(func.coalesce(Supplier.alternate_phone, "").contains(digits, autoescape=True))
    return or_(*clauses)


def list_suppliers(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    active: bool | None = True,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[SupplierView], int]:
    conditions: list[ColumnElement[bool]] = [Supplier.shop_id == shop_id]
    if q and q.strip():
        conditions.append(_search_clause(q))
    if active is not None:
        conditions.append(Supplier.is_active.is_(active))

    total = session.scalar(select(func.count()).select_from(Supplier).where(*conditions))
    query = (
        select(Supplier, _product_count())
        .where(*conditions)
        .order_by(func.lower(Supplier.name), Supplier.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return [SupplierView(supplier=row[0], product_count=row[1]) for row in session.execute(query)], total


def list_supplier_options(session: Session, shop_id: int) -> list[Supplier]:
    """Every active supplier (id and name are enough), for pickers such as a product's default supplier."""
    query = (
        select(Supplier)
        .where(Supplier.shop_id == shop_id, Supplier.is_active.is_(True))
        .order_by(func.lower(Supplier.name), Supplier.id)
    )
    return list(session.scalars(query))


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
            raise InvalidInputError("Enter the supplier's name.", field="name")
        return cleaned

    clean("name", name)
    clean("phone", lambda v: normalize_phone(v, field="phone"))
    clean("alternate_phone", lambda v: normalize_phone(v, field="alternate_phone"))
    clean("email", normalize_email)
    clean("gstin", normalize_gstin)
    clean("address", blank_to_none)
    clean("notes", blank_to_none)

    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


def _duplicate_warnings(
    session: Session, shop_id: int, values: dict[str, Any], *, exclude_id: int | None
) -> list[str]:
    """Tell the user about likely duplicates. Never blocks: real suppliers can share names or phones."""
    warnings: list[str] = []

    def others() -> Any:
        query = select(Supplier)
        query = query.where(Supplier.shop_id == shop_id)
        return query.where(Supplier.id != exclude_id) if exclude_id is not None else query

    if values.get("name"):
        same_name = session.scalar(
            others()
            .where(func.lower(Supplier.name) == values["name"].lower())
            .with_only_columns(Supplier.name)
            .limit(1)
        )
        if same_name is not None:
            warnings.append(f"Another supplier is also named '{same_name}'.")

    phones = {values[key] for key in ("phone", "alternate_phone") if values.get(key)}
    if phones:
        match = session.execute(
            others()
            .where(or_(Supplier.phone.in_(phones), Supplier.alternate_phone.in_(phones)))
            .with_only_columns(Supplier.name, Supplier.phone)
            .limit(1)
        ).first()
        if match is not None:
            warnings.append(f"A phone number you entered is also used by '{match[0]}'.")

    if values.get("gstin"):
        owner = session.scalar(
            others().where(Supplier.gstin == values["gstin"]).with_only_columns(Supplier.name).limit(1)
        )
        if owner is not None:
            warnings.append(f"The GSTIN {values['gstin']} is also used by '{owner}'.")
    return warnings


def _snapshot(supplier: Supplier) -> dict[str, Any]:
    return {name: getattr(supplier, name) for name in AUDITED_FIELDS}


# --- Writing -------------------------------------------------------------------------------------


def create_supplier(session: Session, ctx: RequestContext, data: dict[str, Any]) -> SaveResult:
    data = {key: data.get(key) for key in UPDATABLE_FIELDS if key in data}
    data.setdefault("name", None)  # a missing name is reported like a blank one
    values = _normalize(data)
    warnings = _duplicate_warnings(session, ctx.shop_id, values, exclude_id=None)

    supplier = Supplier(shop_id=ctx.shop_id, **values)
    session.add(supplier)
    session.flush()
    session.refresh(supplier)
    record_audit(
        session,
        ctx,
        entity_type="supplier",
        entity_id=supplier.id,
        action="create",
        after=_snapshot(supplier),
    )
    return SaveResult(get_supplier_view(session, ctx.shop_id, supplier.id), warnings)


def update_supplier(
    session: Session, ctx: RequestContext, supplier_id: int, changes: dict[str, Any]
) -> SaveResult:
    """Change some details. Only fields present in `changes` are touched; `null` clears an optional field."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidInputError(f"These fields cannot be changed: {', '.join(sorted(unknown))}.")
    if "name" in changes and changes["name"] is None:
        raise InvalidInputError("Enter the supplier's name.", field="name")

    supplier = get_supplier(session, ctx.shop_id, supplier_id)
    changes = _normalize(changes)
    changes = {key: value for key, value in changes.items() if getattr(supplier, key) != value}
    warnings: list[str] = []

    if changes:
        warnings = _duplicate_warnings(session, ctx.shop_id, changes, exclude_id=supplier.id)
        before = {key: getattr(supplier, key) for key in changes}
        for key, value in changes.items():
            setattr(supplier, key, value)
        session.flush()
        session.refresh(supplier)
        record_audit(
            session,
            ctx,
            entity_type="supplier",
            entity_id=supplier.id,
            action="update",
            before=before,
            after={key: getattr(supplier, key) for key in changes},
        )
    return SaveResult(get_supplier_view(session, ctx.shop_id, supplier.id), warnings)


def set_supplier_active(
    session: Session, ctx: RequestContext, supplier_id: int, *, active: bool
) -> SupplierView:
    """Activate or deactivate. Products keep their default-supplier link; repeating a call is harmless."""
    supplier = get_supplier(session, ctx.shop_id, supplier_id)
    if supplier.is_active != active:
        supplier.is_active = active
        session.flush()
        record_audit(
            session,
            ctx,
            entity_type="supplier",
            entity_id=supplier.id,
            action="activate" if active else "deactivate",
            before={"is_active": not active},
            after={"is_active": active},
        )
    return get_supplier_view(session, ctx.shop_id, supplier.id)
