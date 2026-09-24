"""Files for an outside accounting system, built from the finance ledger and the documents that already exist.

Nothing here is a second ledger and nothing claims to match a particular accounting product: the files are plain CSV or Excel with clear
column names, and each row can carry the code YOUR accounting system uses (a mapping you set: see `AccountingMapping`). A row whose kind has
no mapping is exported with a blank code and marked UNMAPPED, so nothing is guessed. Import into a specific product is a mapping exercise
that has not been tested against any product and is therefore not claimed.

Kinds: transactions (the finance ledger), invoices (posted sales and quick sales), customers, suppliers, tax (the tax reporting summary).
Mapping keys: `EVENT:<finance event type>` (the account for that kind of money event) and `METHOD:<payment method>` (the cash or bank account).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import AccountingMapping, Customer, QuickSale, Sale, Supplier
from app.models.enums import FinanceEventType, FinancePaymentMethod, SaleStatus
from app.services import finance_ledger_service, tax_service
from app.services.audit_service import record_audit
from app.services.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.services.export_service import (
    CSV_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    Column,
    ExportFile,
    Kind,
    ReportFormat,
    render_report_csv,
    render_report_xlsx,
)
from app.services.shop_service import get_shop, shop_today

KINDS = {
    "transactions": ("FINANCE_VIEW", "FINANCE_EXPORT"),
    "invoices": ("REPORT_VIEW", "FINANCE_EXPORT"),
    "customers": ("CUSTOMER_VIEW", "FINANCE_EXPORT"),
    "suppliers": ("SUPPLIER_VIEW", "FINANCE_EXPORT"),
    "tax": ("FINANCE_VIEW", "FINANCE_EXPORT"),
}
MAX_ROWS = 50_000
VALID_KEYS = (
    {f"EVENT:{e.value}" for e in FinanceEventType}
    | {f"METHOD:{m.value}" for m in FinancePaymentMethod}
    | {"METHOD:NOT_RECORDED"}
)


def list_mappings(session: Session, shop_id: int) -> list[AccountingMapping]:
    return list(
        session.scalars(
            select(AccountingMapping)
            .where(AccountingMapping.shop_id == shop_id)
            .order_by(AccountingMapping.source_key)
        )
    )


def set_mapping(
    session: Session,
    ctx: RequestContext,
    source_key: str,
    external_code: str,
    external_name: str | None = None,
) -> AccountingMapping:
    if source_key not in VALID_KEYS:
        raise InvalidInputError(
            "Unknown source. Use EVENT:<event type> or METHOD:<payment method>.", field="source_key"
        )
    code = (external_code or "").strip()
    if not code or len(code) > 60 or any(ord(c) < 32 for c in code + (external_name or "")):
        raise InvalidInputError(
            "Give the account code from your accounting system (up to 60 characters).", field="external_code"
        )
    row = session.scalar(
        select(AccountingMapping).where(
            AccountingMapping.shop_id == ctx.shop_id, AccountingMapping.source_key == source_key
        )
    )
    if row is None:
        row = AccountingMapping(shop_id=ctx.shop_id, source_key=source_key, external_code=code)
        session.add(row)
    row.external_code, row.external_name = code, (external_name or "").strip()[:120] or None
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="accounting_mapping",
        entity_id=row.id,
        action="accounting_mapping_set",
        after={"key": source_key},
    )
    return row


def clear_mapping(session: Session, ctx: RequestContext, source_key: str) -> None:
    row = session.scalar(
        select(AccountingMapping).where(
            AccountingMapping.shop_id == ctx.shop_id, AccountingMapping.source_key == source_key
        )
    )
    if row is None:
        raise NotFoundError("No mapping for that source.")
    session.delete(row)
    record_audit(
        session,
        ctx,
        entity_type="accounting_mapping",
        entity_id=row.id,
        action="accounting_mapping_cleared",
        after={"key": source_key},
    )


def _codes(session: Session, shop_id: int) -> dict[str, str]:
    return {m.source_key: m.external_code for m in list_mappings(session, shop_id)}


def build(
    session: Session, ctx: RequestContext, kind: str, date_from: date, date_to: date
) -> tuple[list[Column], list[dict], list[str]]:
    if kind not in KINDS:
        raise NotFoundError(f"Unknown export '{kind}'.")
    if not set(KINDS[kind]) <= set(ctx.granted):
        raise ForbiddenError("Your role cannot export this.", code="permission_denied")
    if date_to < date_from:
        raise InvalidInputError("The end date is before the start date.", field="date_to")
    shop_id, notes = ctx.shop_id, []
    if kind == "transactions":
        codes = _codes(session, shop_id)
        rows = []
        for r in reversed(finance_ledger_service.movements(session, shop_id, date_from, date_to)):
            account, method = (
                codes.get(f"EVENT:{r.event_type.value}"),
                codes.get(f"METHOD:{r.payment_method}"),
            )
            rows.append({
                "date": r.entry_date, "source": r.source_type, "source_id": r.source_id, "reference": r.reference or "", "event_type": r.event_type.value,
                "direction": r.direction.value, "amount": r.amount, "settled_amount": r.settled_amount, "payment_method": r.payment_method,
                "account_code": account or "", "payment_account_code": method or "", "ledger_status": r.status,
                "mapping_status": "MAPPED" if account else "UNMAPPED",
            })  # fmt: skip
        cols = [
            Column("date", "Date", Kind.DATE),
            Column("source", "Source"),
            Column("source_id", "Source id", Kind.INTEGER),
            Column("reference", "Reference"),
            Column("event_type", "Event"),
            Column("direction", "Direction"),
            Column("amount", "Amount", Kind.MONEY),
            Column("settled_amount", "Settled amount", Kind.MONEY),
            Column("payment_method", "Payment method"),
            Column("account_code", "Account code"),
            Column("payment_account_code", "Payment account code"),
            Column("ledger_status", "Ledger status"),
            Column("mapping_status", "Mapping"),
        ]
        unmapped = sum(1 for r in rows if r["mapping_status"] == "UNMAPPED")
        notes.append(
            f"{unmapped} of {len(rows)} rows have no account code: set the mapping for their event type. Reversed rows are included with their reversal."
        )
    elif kind == "invoices":
        names = dict(
            session.execute(select(Customer.id, Customer.name).where(Customer.shop_id == shop_id)).all()
        )
        rows = []
        for model, label, no_col in (
            (Sale, "Detailed sale", Sale.invoice_no),
            (QuickSale, "Quick sale", QuickSale.quick_no),
        ):
            for d, no, cid, total, paid, method in session.execute(
                select(model.sale_date, no_col, model.customer_id, model.total_amount, model.amount_paid, model.payment_method)
                .where(model.shop_id == shop_id, model.status == SaleStatus.POSTED, model.sale_date >= date_from, model.sale_date <= date_to)
                .order_by(model.sale_date, model.id).limit(MAX_ROWS)
            ):  # fmt: skip
                rows.append(
                    {
                        "date": d,
                        "number": no or "",
                        "kind": label,
                        "customer": names.get(cid, "") if cid else "",
                        "total": total,
                        "paid": total if paid is None else paid,
                        "payment_method": method.value if method else "",
                    }
                )
        rows.sort(key=lambda r: (r["date"], r["number"]))
        cols = [
            Column("date", "Date", Kind.DATE),
            Column("number", "Invoice number"),
            Column("kind", "Kind"),
            Column("customer", "Customer"),
            Column("total", "Total", Kind.MONEY),
            Column("paid", "Paid", Kind.MONEY),
            Column("payment_method", "Payment method"),
        ]
        notes.append(
            "Posted documents only. Quick sales have no line items and are exported as a single total."
        )
    elif kind == "customers":
        rows = [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone or "",
                "email": c.email or "",
                "active": "yes" if c.is_active else "no",
            }
            for c in session.scalars(
                select(Customer).where(Customer.shop_id == shop_id).order_by(Customer.name).limit(MAX_ROWS)
            )
        ]
        cols = [
            Column("id", "Customer id", Kind.INTEGER),
            Column("name", "Name"),
            Column("phone", "Phone"),
            Column("email", "Email"),
            Column("active", "Active"),
        ]
        notes.append("Contains personal contact details: share only with your accountant.")
    elif kind == "suppliers":
        rows = [
            {
                "id": s.id,
                "name": s.name,
                "phone": s.phone or "",
                "email": s.email or "",
                "active": "yes" if s.is_active else "no",
            }
            for s in session.scalars(
                select(Supplier).where(Supplier.shop_id == shop_id).order_by(Supplier.name).limit(MAX_ROWS)
            )
        ]
        cols = [
            Column("id", "Supplier id", Kind.INTEGER),
            Column("name", "Name"),
            Column("phone", "Phone"),
            Column("email", "Email"),
            Column("active", "Active"),
        ]
    else:
        summary = tax_service.summary(session, shop_id, date_from, date_to)
        rows = []
        if summary.status == "CONFIGURED":
            for section_name in ("sales", "sales_returns", "purchases", "purchase_returns"):
                section = getattr(summary, section_name)
                for b in section.buckets if section else []:
                    rows.append(
                        {
                            "section": section_name,
                            "rate": b.rate_name,
                            "rate_bp": b.rate_bp,
                            "taxable": b.taxable_amount,
                            "tax": b.tax_amount,
                            "gross": b.gross_amount,
                        }
                    )
        cols = [
            Column("section", "Section"),
            Column("rate", "Rate name"),
            Column("rate_bp", "Rate (basis points)", Kind.INTEGER),
            Column("taxable", "Taxable amount", Kind.MONEY),
            Column("tax", "Tax amount", Kind.MONEY),
            Column("gross", "Gross amount", Kind.MONEY),
        ]
        notes.append(summary.disclaimer)
        if summary.status != "CONFIGURED":
            notes.append(
                "Tax reporting is not configured for this shop, so there are no rows: nothing is guessed."
            )
    notes.append(
        "Generated from the shop's own records. Compatibility with a specific accounting product has not been tested."
    )
    return cols, rows[:MAX_ROWS], notes


def render(
    session: Session, ctx: RequestContext, kind: str, date_from: date, date_to: date, fmt: ReportFormat
) -> ExportFile:
    """The file. PDF is not offered for an accounting hand-over: use CSV or Excel."""
    if fmt is ReportFormat.PDF:
        raise InvalidInputError("Choose csv or xlsx for an accounting export.", field="format")
    cols, rows, notes = build(session, ctx, kind, date_from, date_to)
    shop = get_shop(session, ctx.shop_id)
    meta = [
        ("Shop", shop.name),
        ("Period", f"{date_from.isoformat()} to {date_to.isoformat()}"),
        ("Generated", utc_now().strftime("%Y-%m-%d %H:%M UTC")),
    ]
    title = f"Accounting export: {kind}"
    stem = f"accounting-{kind}_{shop_today(shop).isoformat()}"
    if fmt is ReportFormat.XLSX:
        return ExportFile(
            f"{stem}.xlsx",
            XLSX_MEDIA_TYPE,
            render_report_xlsx(title, meta, cols, rows, notes, sheet_name=kind.title()),
        )
    return ExportFile(f"{stem}.csv", CSV_MEDIA_TYPE, render_report_csv(title, meta, cols, rows, notes))
