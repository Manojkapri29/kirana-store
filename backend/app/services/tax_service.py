"""A configurable TAX REPORTING FOUNDATION (GST/VAT/sales-tax ready). It is not a tax filing, does not claim
compliance with any jurisdiction, and builds in no country's rules: the shop chooses the tax type, whether its
prices include tax, and the rates (per product category, plus an optional default).

Method, stated on every report:
  * Sales and returns are taxed line by line from Detailed Sales. A bill-level discount is spread across the
    lines in proportion to their value, so the taxable base matches what the customer really paid.
  * Purchases and purchase returns are taxed the same way from their lines.
  * The rate of a line is its product category's active rate, else the shop's default rate, else NONE: a line
    with no rate is reported as UNCLASSIFIED, never taxed at an assumed rate.
  * Quick Sales are a bare total with no product lines, so they cannot be classified: their amount is reported
    as "Not Available" for tax, never guessed.
  * Inclusive prices: taxable = amount x 10000 / (10000 + rate_bp), tax = amount - taxable.
    Exclusive prices: taxable = amount, tax = amount x rate_bp / 10000. Rounded half-up to the paisa.
Until tax settings exist the report says "NOT_CONFIGURED" and every tax figure is null.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import (
    Category,
    Product,
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    PurchaseReturnItem,
    QuickSale,
    Sale,
    SaleItem,
    SalesReturn,
    SalesReturnItem,
    TaxRate,
    TaxSetting,
)
from app.models.enums import DocumentStatus, PurchaseStatus, SaleStatus, TaxType
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
DISCLAIMER = (
    "Tax reporting foundation. These figures are computed from your configured rates and are not a tax return "
    "or a statement of legal or tax compliance."
)
NOT_CONFIGURED = "NOT_CONFIGURED"
NO_RATE = "UNCLASSIFIED"


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- Configuration --------------------------------------------------------------------------------------------


def get_settings(session: Session, shop_id: int) -> TaxSetting | None:
    return session.scalar(select(TaxSetting).where(TaxSetting.shop_id == shop_id))


def configure(
    session: Session, ctx: RequestContext, *, tax_type: TaxType, registration_number: str | None,
    location_state: str | None, prices_include_tax: bool,
) -> TaxSetting:  # fmt: skip
    reg = (registration_number or "").strip() or None
    if reg and len(reg) > 30:
        raise InvalidInputError("The registration number is too long.", field="registration_number")
    row = get_settings(session, ctx.shop_id)
    before = None
    if row is None:
        row = TaxSetting(shop_id=ctx.shop_id, tax_type=tax_type, prices_include_tax=prices_include_tax)
        session.add(row)
    else:
        before = {"tax_type": row.tax_type, "registration_number": row.registration_number,
                  "location_state": row.location_state, "prices_include_tax": row.prices_include_tax}  # fmt: skip
    row.tax_type = tax_type
    row.registration_number = reg
    row.location_state = (location_state or "").strip() or None
    row.prices_include_tax = prices_include_tax
    session.flush()
    record_audit(
        session, ctx, entity_type="tax_setting", entity_id=row.id, action="tax_settings_changed", before=before,
        after={"tax_type": tax_type, "location_state": row.location_state, "prices_include_tax": prices_include_tax},
    )  # fmt: skip
    return row


def list_rates(session: Session, shop_id: int, *, active_only: bool = False) -> list[TaxRate]:
    query = select(TaxRate).where(TaxRate.shop_id == shop_id).order_by(TaxRate.name)
    if active_only:
        query = query.where(TaxRate.is_active.is_(True))
    return list(session.scalars(query))


def _basis_points(rate_percent: Decimal) -> int:
    if (
        isinstance(rate_percent, bool)
        or isinstance(rate_percent, float)
        or not isinstance(rate_percent, Decimal | int)
    ):
        raise InvalidInputError("Enter the rate as a number, for example 18 or 2.5.", field="rate_percent")
    rate = Decimal(rate_percent)
    bp = rate * 100
    if bp != bp.to_integral_value() or not (Decimal(0) <= bp <= Decimal(10000)):
        raise InvalidInputError(
            "The rate must be between 0 and 100 with at most 2 decimals.", field="rate_percent"
        )
    return int(bp)


def create_rate(
    session: Session, ctx: RequestContext, *, name: str, rate_percent: Decimal, category_id: int | None = None,
    tax_category: str | None = None,
) -> TaxRate:  # fmt: skip
    name = name.strip()
    if not name:
        raise InvalidInputError("Give the rate a name.", field="name")
    bp = _basis_points(rate_percent)
    if (
        category_id is not None
        and session.scalar(
            select(Category.id).where(Category.shop_id == ctx.shop_id, Category.id == category_id)
        )
        is None
    ):
        raise NotFoundError("Product category not found")
    if category_id is None and any(
        r.category_id is None and r.is_active for r in list_rates(session, ctx.shop_id)
    ):
        raise ConflictError("There is already an active default rate.", code="duplicate_default")
    if category_id is not None and any(
        r.category_id == category_id and r.is_active for r in list_rates(session, ctx.shop_id)
    ):
        raise ConflictError("This category already has an active rate.", code="duplicate_category")
    if any(r.name.lower() == name.lower() for r in list_rates(session, ctx.shop_id)):
        raise ConflictError("A rate with this name already exists.", field="name", code="duplicate_record")
    rate = TaxRate(
        shop_id=ctx.shop_id, name=name, rate_bp=bp, category_id=category_id,
        tax_category=(tax_category or "").strip() or None,
    )  # fmt: skip
    session.add(rate)
    session.flush()
    record_audit(
        session, ctx, entity_type="tax_rate", entity_id=rate.id, action="tax_rate_created",
        after={"name": name, "rate_bp": bp, "category_id": category_id},
    )  # fmt: skip
    return rate


def update_rate(
    session: Session, ctx: RequestContext, rate_id: int, *, rate_percent: Decimal | None = None,
    is_active: bool | None = None, tax_category: str | None = None,
) -> TaxRate:  # fmt: skip
    """Change a rate or switch it off. A rate is never deleted: past reports keep the rates they used."""
    rate = session.scalar(select(TaxRate).where(TaxRate.shop_id == ctx.shop_id, TaxRate.id == rate_id))
    if rate is None:
        raise NotFoundError("Tax rate not found")
    before = {"rate_bp": rate.rate_bp, "is_active": rate.is_active}
    if rate_percent is not None:
        rate.rate_bp = _basis_points(rate_percent)
    if is_active is not None:
        rate.is_active = is_active
    if tax_category is not None:
        rate.tax_category = tax_category.strip() or None
    session.flush()
    record_audit(
        session, ctx, entity_type="tax_rate", entity_id=rate.id, action="tax_rate_changed", before=before,
        after={"rate_bp": rate.rate_bp, "is_active": rate.is_active},
    )  # fmt: skip
    return rate


# --- Reporting --------------------------------------------------------------------------------------------------


@dataclass
class Bucket:
    rate_name: str
    rate_bp: int | None
    tax_category: str | None
    taxable_amount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    gross_amount: Decimal = ZERO


@dataclass(frozen=True)
class TaxSection:
    buckets: list[Bucket]
    taxable_amount: Decimal
    tax_amount: Decimal
    unclassified_amount: Decimal  # amounts with no rate: not taxed, listed so nothing is hidden


@dataclass(frozen=True)
class TaxSummary:
    status: str
    date_from: date
    date_to: date
    tax_type: str | None
    registration_number: str | None
    location_state: str | None
    prices_include_tax: bool | None
    sales: TaxSection | None
    sales_returns: TaxSection | None
    purchases: TaxSection | None
    purchase_returns: TaxSection | None
    tax_collected: Decimal | None  # on sales, less tax on returns
    tax_paid: Decimal | None  # on purchases, less tax on purchase returns
    net_tax: Decimal | None  # collected - paid (indicative only)
    quick_sales_amount: Decimal  # cannot be classified: "Not Available" for tax
    methodology: str
    disclaimer: str = DISCLAIMER
    notes: list[str] = field(default_factory=list)


def _split(amount: Decimal, bp: int, inclusive: bool) -> tuple[Decimal, Decimal]:
    if inclusive:
        taxable = _q(amount * 10000 / (10000 + bp))
        return taxable, amount - taxable
    return amount, _q(amount * bp / 10000)


def _allocate(lines: list[tuple[int, Decimal]], bill_total: Decimal) -> list[tuple[int, Decimal]]:
    """Spread a bill's discount over its lines pro rata so the line bases add up to the bill total exactly."""
    subtotal = sum((v for _, v in lines), ZERO)
    if subtotal <= 0 or subtotal == bill_total:
        return lines
    out, used = [], ZERO
    for i, (pid, value) in enumerate(lines):
        share = bill_total - used if i == len(lines) - 1 else _q(value * bill_total / subtotal)
        used += share
        out.append((pid, share))
    return out


def _section(
    items: list[tuple[int, Decimal]], rate_of: dict[int, TaxRate | None], inclusive: bool
) -> TaxSection:
    buckets: dict[str, Bucket] = {}
    unclassified = ZERO
    for product_id, amount in items:
        rate = rate_of.get(product_id)
        if rate is None:
            unclassified += amount
            continue
        b = buckets.setdefault(rate.name, Bucket(rate.name, rate.rate_bp, rate.tax_category))
        taxable, tax = _split(amount, rate.rate_bp, inclusive)
        b.taxable_amount += taxable
        b.tax_amount += tax
        b.gross_amount += amount
    rows = sorted(buckets.values(), key=lambda b: b.rate_name)
    return TaxSection(
        rows,
        sum((b.taxable_amount for b in rows), ZERO),
        sum((b.tax_amount for b in rows), ZERO),
        unclassified,
    )


def summary(session: Session, shop_id: int, date_from: date, date_to: date) -> TaxSummary:
    settings = get_settings(session, shop_id)
    quick = sum(
        (
            q.total_amount
            for q in session.scalars(
                select(QuickSale).where(
                    QuickSale.shop_id == shop_id,
                    QuickSale.status == SaleStatus.POSTED,
                    QuickSale.sale_date >= date_from,
                    QuickSale.sale_date <= date_to,
                )
            )
        ),
        ZERO,
    )
    if settings is None:
        return TaxSummary(
            NOT_CONFIGURED, date_from, date_to, None, None, None, None, None, None, None, None, None, None, None,
            quick, "Tax settings have not been configured, so no tax can be calculated.",
            notes=["Configure the tax type, prices basis and rates first."],
        )  # fmt: skip
    inclusive = settings.prices_include_tax
    rates = list_rates(session, shop_id, active_only=True)
    by_category = {r.category_id: r for r in rates if r.category_id is not None}
    default = next((r for r in rates if r.category_id is None), None)
    category_of = dict(
        session.execute(select(Product.id, Product.category_id).where(Product.shop_id == shop_id)).all()
    )

    def rate_for(product_id: int) -> TaxRate | None:
        return by_category.get(category_of.get(product_id)) or default

    rate_of = {pid: rate_for(pid) for pid in category_of}

    def bills(
        model, item_model, fk, date_col, status_col, status_val, total_col, item_amount
    ) -> list[tuple[int, Decimal]]:
        out: list[tuple[int, Decimal]] = []
        docs = session.scalars(
            select(model).where(
                model.shop_id == shop_id, status_col == status_val, date_col >= date_from, date_col <= date_to
            )
        ).all()
        for doc in docs:
            lines = [
                (i.product_id, item_amount(i))
                for i in session.scalars(
                    select(item_model).where(item_model.shop_id == shop_id, getattr(item_model, fk) == doc.id)
                )
            ]
            out.extend(_allocate(lines, getattr(doc, total_col)) if total_col else lines)
        return out

    sales_items = bills(
        Sale,
        SaleItem,
        "sale_id",
        Sale.sale_date,
        Sale.status,
        SaleStatus.POSTED,
        "total_amount",
        lambda i: i.line_total,
    )
    purchase_items = bills(
        Purchase,
        PurchaseItem,
        "purchase_id",
        Purchase.purchase_date,
        Purchase.status,
        PurchaseStatus.POSTED,
        "total_amount",
        lambda i: i.line_total,
    )
    sret_items = bills(
        SalesReturn,
        SalesReturnItem,
        "sales_return_id",
        SalesReturn.return_date,
        SalesReturn.status,
        DocumentStatus.POSTED,
        None,
        lambda i: i.refund_amount,
    )
    pret_items = bills(
        PurchaseReturn,
        PurchaseReturnItem,
        "purchase_return_id",
        PurchaseReturn.return_date,
        PurchaseReturn.status,
        DocumentStatus.POSTED,
        None,
        lambda i: i.line_total,
    )

    sales = _section(sales_items, rate_of, inclusive)
    sales_returns = _section(sret_items, rate_of, inclusive)
    purchases = _section(purchase_items, rate_of, inclusive)
    purchase_returns = _section(pret_items, rate_of, inclusive)
    collected = sales.tax_amount - sales_returns.tax_amount
    paid = purchases.tax_amount - purchase_returns.tax_amount
    notes = []
    if quick > 0:
        notes.append(
            "Quick Sales have no product lines, so their tax is Not Available and they are left out."
        )
    if sales.unclassified_amount or purchases.unclassified_amount:
        notes.append(
            "Some lines have no tax rate configured for their category and are listed as unclassified, untaxed."
        )
    if not rates:
        notes.append("No active tax rates are configured; every line is unclassified.")
    method = (
        f"{settings.tax_type.value} reporting foundation; prices are treated as tax-{'inclusive' if inclusive else 'exclusive'}. "
        "Each line uses its category's rate, else the default rate, else none."
    )
    return TaxSummary(
        "CONFIGURED", date_from, date_to, settings.tax_type.value, settings.registration_number,
        settings.location_state, inclusive, sales, sales_returns, purchases, purchase_returns, collected, paid,
        collected - paid, quick, method, notes=notes,
    )  # fmt: skip
