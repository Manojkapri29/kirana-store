"""The reports that return a `ReportTable`, by key: one registry that exports, scheduled reports and the AI assistant share.

Each entry names the report, the permissions needed to read it (the same ones its JSON endpoint requires), and the function
that runs it. Nothing here is computed differently from the endpoint: an export of `sales-trend-month` runs exactly the
function behind `GET /analytics/sales/trend?bucket=month`, so a figure on screen and the same figure in a file cannot differ.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.reporting import cohorts, customers, inventory, kpis, sales, suppliers
from app.reporting import finance as finance_reports
from app.reporting.filters import ReportFilters, ReportTable
from app.services.errors import ForbiddenError, NotFoundError

Run = Callable[[Session, int, ReportFilters, date], ReportTable]


@dataclass(frozen=True)
class ReportSpec:
    key: str
    label: str
    permissions: tuple[str, ...]
    run: Run


_SALES = ("ANALYTICS_VIEW", "REPORT_VIEW")
_INV = ("ANALYTICS_ADVANCED", "INVENTORY_VIEW")
_CRM = ("ANALYTICS_ADVANCED", "CRM_ANALYTICS_VIEW")
_SUP = ("ANALYTICS_ADVANCED", "SUPPLIER_VIEW", "PURCHASE_VIEW")
_FIN = ("ANALYTICS_ADVANCED", "FINANCE_VIEW")

REPORTS: dict[str, ReportSpec] = {}


def _add(key: str, label: str, permissions: tuple[str, ...], run: Run) -> None:
    REPORTS[key] = ReportSpec(key, label, permissions, run)


def _kpi_table(
    session: Session, shop_id: int, f: ReportFilters, today: date, granted: set[str] | None = None
) -> ReportTable:
    """The KPI list as a table: the very values `GET /analytics/kpis` returns."""
    rep = kpis.report(session, shop_id, f, today, None, granted)
    rows = []
    for r in rep.kpis:
        rows.append(
            {
                "kpi": r.definition.name,
                "unit": r.definition.unit,
                "value": r.current.amount,
                "availability": r.current.availability.value,
                "reason": r.current.reason or "",
                "previous": r.previous.amount if r.previous else None,
                "change": r.change.absolute if r.change else None,
                "change_pct": r.change.percent if r.change else None,
                "change_note": (r.change.note or "") if r.change else "",
                "formula": r.definition.formula,
                "source": r.definition.source,
            }
        )
    cols = [
        ("kpi", "KPI", "text"),
        ("unit", "Unit", "text"),
        ("value", "Value", "decimal"),
        ("availability", "Availability", "text"),
        ("reason", "Reason if not available", "text"),
        ("previous", "Previous period", "decimal"),
        ("change", "Change", "decimal"),
        ("change_pct", "Change %", "percent"),
        ("change_note", "Change note", "text"),
        ("formula", "Formula", "text"),
        ("source", "Source", "text"),
    ]
    return ReportTable(
        columns=cols,
        rows=rows,
        total=len(rows),
        limit=f.limit,
        offset=f.offset,
        title="Key performance indicators",
        period=rep.period,
        comparison=rep.comparison,
        notes=rep.notes,
        source="The KPI framework",
    )


_add(
    "kpis", "Key performance indicators", ("ANALYTICS_VIEW",), lambda s, shop, f, t: _kpi_table(s, shop, f, t)
)
for _bucket in ("day", "week", "month"):
    _add(
        f"sales-trend-{_bucket}",
        f"Sales by {_bucket}",
        _SALES,
        lambda s, shop, f, t, b=_bucket: sales.trend(s, shop, f, b),
    )
_add(
    "sales-products-top",
    "Top-selling products",
    _SALES,
    lambda s, shop, f, t: sales.products(s, shop, f, "top"),
)
_add(
    "sales-products-bottom",
    "Slowest-selling products",
    _SALES,
    lambda s, shop, f, t: sales.products(s, shop, f, "bottom"),
)
_add("sales-categories", "Sales by category", _SALES, lambda s, shop, f, t: sales.categories(s, shop, f))
_add("sales-brands", "Sales by brand", _SALES, lambda s, shop, f, t: sales.brands(s, shop, f))
_add("sales-channels", "Sales by channel", _SALES, lambda s, shop, f, t: sales.channels(s, shop, f))
_add(
    "sales-payment-methods",
    "Sales by payment method",
    _SALES,
    lambda s, shop, f, t: sales.payment_methods(s, shop, f),
)
_add("sales-discounts", "Discounts given", _SALES, lambda s, shop, f, t: sales.discounts(s, shop, f))
_add("sales-promotions", "Promotion-linked sales", _SALES, lambda s, shop, f, t: sales.promotions(s, shop, f))
_add("inventory-stock", "Current stock", _INV, lambda s, shop, f, t: inventory.stock(s, shop, f))
_add(
    "inventory-categories",
    "Inventory by category",
    _INV,
    lambda s, shop, f, t: inventory.category_summary(s, shop, f),
)
_add("inventory-turnover", "Stock turnover", _INV, lambda s, shop, f, t: inventory.turnover(s, shop, f))
for _kind in ("fast", "slow", "dead"):
    _add(
        f"inventory-movers-{_kind}",
        f"{_kind.title()}-moving stock",
        _INV,
        lambda s, shop, f, t, k=_kind: inventory.movers(s, shop, f, k),
    )
_add("inventory-aging", "Stock ageing", _INV, lambda s, shop, f, t: inventory.aging(s, shop, f))
_add("inventory-movement", "Stock movement", _INV, lambda s, shop, f, t: inventory.movement(s, shop, f))
_add(
    "inventory-purchase-vs-sales",
    "Purchases against sales",
    _INV,
    lambda s, shop, f, t: inventory.purchase_vs_sales(s, shop, f),
)
_add(
    "inventory-adjustments",
    "Stock adjustments",
    _INV,
    lambda s, shop, f, t: inventory.adjustments(s, shop, f),
)
_add(
    "inventory-count-variance",
    "Stock count variance",
    _INV,
    lambda s, shop, f, t: inventory.count_variance(s, shop, f),
)
_add(
    "inventory-reorder", "Reorder recommendations", _INV, lambda s, shop, f, t: inventory.reorder(s, shop, f)
)
_add("inventory-stock-outs", "Stock-outs", _INV, lambda s, shop, f, t: inventory.stock_outs(s, shop, f))
_add("customers-list", "Customers by revenue", _CRM, lambda s, shop, f, t: customers.customers(s, shop, f, t))
_add(
    "customers-segments",
    "Revenue by customer segment",
    _CRM,
    lambda s, shop, f, t: customers.segments(s, shop, f, t),
)
_add("customers-loyalty", "Loyalty activity", _CRM, lambda s, shop, f, t: customers.loyalty(s, shop, f))
_add(
    "customers-campaigns", "Campaign performance", _CRM, lambda s, shop, f, t: customers.campaigns(s, shop, f)
)
_add("customers-referrals", "Referrals", _CRM, lambda s, shop, f, t: customers.referrals(s, shop, f))
_add("cohorts", "Cohort retention", _CRM, lambda s, shop, f, t: cohorts.cohorts(s, shop, f, t))
_add("suppliers-spend", "Spend by supplier", _SUP, lambda s, shop, f, t: suppliers.suppliers(s, shop, f))
_add(
    "suppliers-products",
    "Purchases by supplier and product",
    _SUP,
    lambda s, shop, f, t: suppliers.products(s, shop, f),
)
_add(
    "suppliers-cost-trend",
    "Purchase cost trend",
    _SUP,
    lambda s, shop, f, t: suppliers.cost_trend(s, shop, f),
)
_add(
    "suppliers-returns",
    "Purchase returns by supplier",
    _SUP,
    lambda s, shop, f, t: suppliers.returns(s, shop, f),
)
_add(
    "finance-trend-month",
    "Finance by month",
    _FIN,
    lambda s, shop, f, t: finance_reports.trend(s, shop, f, "month"),
)
_add(
    "finance-trend-day",
    "Finance by day (up to 62 days)",
    _FIN,
    lambda s, shop, f, t: finance_reports.trend(s, shop, f, "day"),
)
_add(
    "finance-expenses",
    "Expenses by category",
    _FIN,
    lambda s, shop, f, t: finance_reports.expenses(s, shop, f),
)
_add(
    "finance-expense-trend",
    "Expenses by month",
    _FIN,
    lambda s, shop, f, t: finance_reports.expense_trend(s, shop, f),
)
_add(
    "finance-payment-mix",
    "Payment method mix",
    _FIN,
    lambda s, shop, f, t: finance_reports.payment_mix(s, shop, f),
)
_add(
    "finance-receivables-aging",
    "Receivables ageing",
    _FIN,
    lambda s, shop, f, t: finance_reports.aging(s, shop, f, "receivables"),
)
_add(
    "finance-payables-aging",
    "Payables ageing",
    _FIN,
    lambda s, shop, f, t: finance_reports.aging(s, shop, f, "payables"),
)


def get(key: str) -> ReportSpec:
    spec = REPORTS.get(key)
    if spec is None:
        raise NotFoundError(f"Unknown report '{key}'.")
    return spec


def run(
    session: Session, shop_id: int, key: str, f: ReportFilters, today: date, granted: set[str] | None
) -> ReportTable:
    """Run a report if `granted` covers what it reads. KPIs the caller may not see are hidden, never zeroed."""
    spec = get(key)
    if granted is not None and not set(spec.permissions) <= granted:
        raise ForbiddenError(
            "Your role does not include the permission to read this report.", code="permission_denied"
        )
    if key == "kpis":
        return _kpi_table(session, shop_id, f, today, granted)
    return spec.run(session, shop_id, f, today)
