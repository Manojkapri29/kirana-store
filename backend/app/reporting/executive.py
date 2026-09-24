"""The executive dashboard: eleven sections, each a short list of KPIs (current, previous comparable period, absolute and
percentage change) built by the KPI framework, plus a small trend where a trend is meaningful. It does its own
arithmetic nowhere: every figure is a KPI, so a dashboard number and the same KPI fetched on its own can never differ, and an
export of the dashboard is generated from these same results.

A section (or a single KPI inside one) the caller has no permission for is HIDDEN, never shown as zero. The dashboard says which
KPI keys were hidden so the screen can explain why a card is missing.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.reporting import kpis
from app.reporting.filters import Period, ReportFilters
from app.services import sales_report_service

SECTIONS: list[tuple[str, str, list[str]]] = [
    ("overview", "Business overview", ["revenue", "orders", "net_profit", "net_cash_flow", "receivables"]),
    (
        "sales",
        "Sales",
        ["revenue", "orders", "units_sold", "average_transaction_value", "sales_growth", "return_rate"],
    ),
    (
        "profitability",
        "Profitability",
        ["cogs", "gross_profit", "gross_margin", "net_profit", "expense_ratio"],
    ),
    (
        "inventory",
        "Inventory",
        ["inventory_value", "stock_turnover", "fast_moving", "slow_moving", "dead_stock", "stock_out_count"],
    ),
    (
        "customers",
        "Customers",
        [
            "new_customers",
            "returning_customers",
            "repeat_purchase_rate",
            "average_customer_value",
            "inactive_customers",
        ],
    ),
    ("suppliers", "Suppliers", ["purchase_value", "payables"]),
    ("finance", "Finance", ["receivables", "payables", "net_cash_flow", "expense_value"]),
    ("online_orders", "Online orders", ["online_orders"]),
    ("promotions", "Promotions", ["promotion_usage", "discount_given"]),
    ("crm", "CRM", ["loyalty_activity", "repeat_purchase_rate", "inactive_customers", "new_customers"]),
    ("operational_health", "Operational health", ["return_rate", "stock_out_count", "dead_stock"]),
]


@dataclass(frozen=True)
class TrendPoint:
    label: str
    value: Decimal


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    kpis: list[kpis.KpiResult]
    hidden_kpis: list[str]
    trend: list[TrendPoint] | None = None
    trend_label: str | None = None


@dataclass(frozen=True)
class ExecutiveDashboard:
    period: Period
    comparison: Period | None
    sections: list[Section]
    notes: list[str] = field(default_factory=list)


def _bucket(period: Period) -> str:
    return "day" if period.days <= 45 else "week" if period.days <= 200 else "month"


def revenue_trend(session: Session, shop_id: int, period: Period) -> list[TrendPoint]:
    """Net sales per day/week/month from the sales report (detailed + quick, before returns)."""
    summary = sales_report_service.sales_summary(session, shop_id, period.start, period.end)
    bucket = _bucket(period)
    grouped: dict[str, Decimal] = {}
    for day in summary.days:
        d = day.day
        key = (
            d.isoformat()
            if bucket == "day"
            else (
                f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
                if bucket == "week"
                else d.strftime("%Y-%m")
            )
        )
        grouped[key] = grouped.get(key, Decimal("0.00")) + day.combined.net
    return [TrendPoint(k, v) for k, v in sorted(grouped.items())]


def build(
    session: Session, shop_id: int, filters: ReportFilters, today: date, granted: set[str] | None = None
) -> ExecutiveDashboard:
    results, hidden = kpis.compute(session, shop_id, filters, today, None, granted)
    by_key = {r.definition.key: r for r in results}
    trend = revenue_trend(session, shop_id, filters.period) if "revenue" in by_key else None
    sections = []
    for key, title, wanted in SECTIONS:
        shown = [by_key[k] for k in wanted if k in by_key]
        sections.append(Section(
            key, title, shown, [k for k in wanted if k in hidden],
            trend if key in ("sales", "overview") and trend is not None else None,
            "Net sales (detailed + quick, before returns)" if key in ("sales", "overview") and trend is not None else None,
        ))  # fmt: skip
    notes = [
        "Every figure is a KPI: see /analytics/kpis/definitions for its formula, source and limitations.",
        "Comparison is against "
        + (filters.comparison.label if filters.comparison else "no period (none chosen)")
        + ".",
    ]
    if filters.comparison is None:
        notes.append("No comparison period was chosen, so no change figures are shown.")
    return ExecutiveDashboard(filters.period, filters.comparison, sections, notes)
