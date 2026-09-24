"""Cross-module observations: facts from two modules placed side by side for the same period.

Wording rule: an insight states what was OBSERVED TOGETHER, ASSOCIATED WITH, or true DURING THE SAME PERIOD. It never says one
thing caused another, never predicts, and never recommends an action. Each insight names its sources and lists what it cannot
say. An insight whose data does not exist is reported as NOT_AVAILABLE or INSUFFICIENT_DATA, never invented.
Each insight needs the data permissions of the modules it reads; one the caller may not use is hidden, not blanked.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.reporting import customers, finance, inventory, kpis, sales, suppliers
from app.reporting.filters import ReportFilters

ZERO = Decimal("0.00")
CAUTION = "This shows what appeared together in the period. It does not show that one caused the other."


@dataclass(frozen=True)
class Insight:
    key: str
    title: str
    availability: str  # AVAILABLE | NOT_AVAILABLE | INSUFFICIENT_DATA
    statement: str
    evidence: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [CAUTION])
    permissions: tuple[str, ...] = ()


def _no(
    key: str, title: str, availability: str, why: str, sources: list[str], perms: tuple[str, ...]
) -> Insight:
    return Insight(key, title, availability, why, [], sources, [CAUTION], perms)


def _high_sales_low_stock(session: Session, shop_id: int, f: ReportFilters) -> Insight:
    key, title, perms = (
        "high_sales_low_stock",
        "Best sellers with low stock",
        ("REPORT_VIEW", "INVENTORY_VIEW"),
    )
    src = ["Detailed sales lines", "Inventory ledger stock"]
    top = sales.products(session, shop_id, ReportFilters(period=f.period, comparison=None, limit=10)).rows
    if not top:
        return _no(key, title, "INSUFFICIENT_DATA", "No detailed product sales in this period.", src, perms)
    stock = {
        r["product_id"]: r
        for r in inventory.stock(
            session, shop_id, ReportFilters(period=f.period, comparison=None, limit=200)
        ).rows
    }
    low = []
    for r in top:
        s = stock.get(r["product_id"])
        if s and s["reorder_level"] is not None and s["current_stock"] <= s["reorder_level"]:
            low.append(
                {
                    "product": r["product"],
                    "quantity_sold": r["quantity"],
                    "current_stock": s["current_stock"],
                    "reorder_level": s["reorder_level"],
                }
            )
    text = (
        f"{len(low)} of the top {len(top)} selling products are at or below their reorder level right now."
        if low
        else f"None of the top {len(top)} selling products is at or below its reorder level right now."
    )
    return Insight(
        key,
        title,
        "AVAILABLE",
        text,
        low,
        src,
        [
            CAUTION,
            "Current stock is compared with the period's sales ranking; reorder levels are set by the shop.",
        ],
        perms,
    )


def _sales_and_cost(session: Session, shop_id: int, f: ReportFilters) -> Insight:
    key, title, perms = (
        "sales_and_purchase_cost",
        "Revenue and purchase cost movement",
        ("REPORT_VIEW", "PURCHASE_VIEW", "SUPPLIER_VIEW"),
    )
    src = ["Sales summary", "Posted purchase lines"]
    if f.comparison is None:
        return _no(
            key,
            title,
            "INSUFFICIENT_DATA",
            "Insufficient comparison data: no comparison period was chosen.",
            src,
            perms,
        )
    cur = sales.summary(session, shop_id, f)["combined_revenue"]
    prev = sales.summary(session, shop_id, ReportFilters(period=f.comparison, comparison=None))[
        "combined_revenue"
    ]
    if prev == 0:
        return _no(
            key,
            title,
            "INSUFFICIENT_DATA",
            "Insufficient comparison data: revenue in the comparison period was zero.",
            src,
            perms,
        )
    moved = [
        r
        for r in suppliers.products(
            session, shop_id, ReportFilters(period=f.period, comparison=None, limit=200)
        ).rows
        if r["cost_change_pct"] is not None and r["cost_change_pct"] > 0
    ]
    pct = ((cur - prev) / prev * 100).quantize(Decimal("0.1"))
    word = "higher" if cur > prev else "lower" if cur < prev else "the same"
    text = (
        f"During the same period, revenue was {abs(pct)}% {word if word != 'the same' else 'unchanged'} than the comparison period and "
        f"{len(moved)} supplier-product pair(s) had a latest purchase cost above their first purchase cost in the period."
    )
    return Insight(
        key,
        title,
        "AVAILABLE",
        text,
        [
            {"product": r["product"], "supplier": r["supplier"], "cost_change_pct": r["cost_change_pct"]}
            for r in moved[:10]
        ],
        src,
        [CAUTION, "Purchase cost change compares first and latest purchase inside the period only."],
        perms,
    )


def _segment_revenue(session: Session, shop_id: int, f: ReportFilters, today: date) -> Insight:
    key, title, perms = "segment_revenue", "Revenue by customer segment", ("CRM_ANALYTICS_VIEW",)
    src = ["CRM segments", "Identified purchases"]
    rows = customers.segments(session, shop_id, ReportFilters(period=f.period, comparison=None), today).rows
    if not rows:
        return _no(
            key, title, "INSUFFICIENT_DATA", "No identified customer purchases in this period.", src, perms
        )
    top = rows[0]
    return Insight(
        key,
        title,
        "AVAILABLE",
        f"Customers in the '{top['segment']}' segment were observed with the most revenue ({top['revenue']}) in the period.",
        [{"segment": r["segment"], "customers": r["customers"], "revenue": r["revenue"]} for r in rows],
        src,
        [CAUTION, "Segments overlap; rows must not be added."],
        perms,
    )


def _promotion(session: Session, shop_id: int, f: ReportFilters) -> Insight:
    key, title, perms = (
        "promotion_associated_revenue",
        "Revenue on bills that used a promotion",
        ("REPORT_VIEW",),
    )
    src = ["Posted detailed sales", "Promotion snapshots on bills"]
    rows = {
        r["promotion"]: r
        for r in sales.promotions(session, shop_id, ReportFilters(period=f.period, comparison=None)).rows
    }
    linked, allb = rows.get("All bills that used any promotion"), rows.get("All detailed bills")
    if not allb or not allb["bills"]:
        return _no(key, title, "INSUFFICIENT_DATA", "No detailed bills in this period.", src, perms)
    text = f"{linked['bills']} of {allb['bills']} detailed bills used a promotion; those bills' revenue was {linked['bill_revenue']} of {allb['bill_revenue']}."
    return Insight(
        key,
        title,
        "AVAILABLE",
        text,
        [
            {
                "bills": linked["bills"],
                "revenue": linked["bill_revenue"],
                "discount_given": linked["discount_given"],
            }
        ],
        src,
        [CAUTION, "The revenue of a whole bill is not revenue caused by the promotion."],
        perms,
    )


def _online_repeat() -> Insight:
    return _no(
        "online_repeat_customers",
        "Online order repeat customers",
        "NOT_AVAILABLE",
        "Not Available: online orders are not connected, so there is no online repeat-customer data.",
        ["Online orders"],
        ("REPORT_VIEW",),
    )


def _revenue_vs_profit(session: Session, shop_id: int, f: ReportFilters) -> Insight:
    key, title, perms = "revenue_and_gross_profit", "Revenue and gross profit", ("FINANCE_VIEW",)
    src = ["Profit and loss (finance ledger)"]
    s = finance.summary(session, shop_id, f)
    pnl = s["pnl"]
    if pnl["gross_profit"] is None:
        return _no(key, title, "NOT_AVAILABLE", s["profit_message"] or finance.NO_PROFIT, src, perms)
    text = f"During the period, revenue was {pnl['revenue']} and gross profit was {pnl['gross_profit']} (margin {pnl['gross_margin_pct']}%)."
    return Insight(
        key,
        title,
        "AVAILABLE",
        text,
        [
            {
                "revenue": pnl["revenue"],
                "gross_profit": pnl["gross_profit"],
                "gross_margin_pct": pnl["gross_margin_pct"],
            }
        ],
        src,
        [CAUTION],
        perms,
    )


def _stock_vs_sales(session: Session, shop_id: int, f: ReportFilters, today: date) -> Insight:
    key, title, perms = (
        "inventory_value_and_sales",
        "Inventory value and sales",
        ("REPORT_VIEW", "INVENTORY_VIEW"),
    )
    src = ["Inventory ledger and costing", "Sales summary"]
    results, _ = kpis.compute(
        session,
        shop_id,
        ReportFilters(period=f.period, comparison=None),
        today,
        ["inventory_value", "revenue"],
        None,
    )
    v = {r.definition.key: r.current for r in results}
    if v["inventory_value"].amount is None:
        return _no(
            key,
            title,
            "NOT_AVAILABLE",
            v["inventory_value"].reason or "Inventory value is not available.",
            src,
            perms,
        )
    rev = v["revenue"].amount or ZERO
    text = f"At the end of the period, stock was valued at {v['inventory_value'].amount} while the period's revenue was {rev}."
    return Insight(
        key,
        title,
        "AVAILABLE",
        text,
        [{"inventory_value": v["inventory_value"].amount, "revenue": rev}],
        src,
        [CAUTION, "Stock value is the current position; revenue is for the period."],
        perms,
    )


INSIGHTS: list[tuple[str, tuple[str, ...]]] = [
    ("high_sales_low_stock", ("REPORT_VIEW", "INVENTORY_VIEW")),
    ("sales_and_purchase_cost", ("REPORT_VIEW", "PURCHASE_VIEW", "SUPPLIER_VIEW")),
    ("segment_revenue", ("CRM_ANALYTICS_VIEW",)),
    ("promotion_associated_revenue", ("REPORT_VIEW",)),
    ("online_repeat_customers", ("REPORT_VIEW",)),
    ("revenue_and_gross_profit", ("FINANCE_VIEW",)),
    ("inventory_value_and_sales", ("REPORT_VIEW", "INVENTORY_VIEW")),
]


def build(
    session: Session, shop_id: int, f: ReportFilters, today: date, granted: set[str] | None
) -> tuple[list[Insight], list[str]]:
    """Every insight the caller may see, and the keys of those hidden for lack of a data permission (never computed)."""
    makers = {
        "high_sales_low_stock": lambda: _high_sales_low_stock(session, shop_id, f),
        "sales_and_purchase_cost": lambda: _sales_and_cost(session, shop_id, f),
        "segment_revenue": lambda: _segment_revenue(session, shop_id, f, today),
        "promotion_associated_revenue": lambda: _promotion(session, shop_id, f),
        "online_repeat_customers": _online_repeat,
        "revenue_and_gross_profit": lambda: _revenue_vs_profit(session, shop_id, f),
        "inventory_value_and_sales": lambda: _stock_vs_sales(session, shop_id, f, today),
    }
    out, hidden = [], []
    for key, perms in INSIGHTS:
        if granted is not None and not set(perms) <= granted:
            hidden.append(key)
        else:
            out.append(makers[key]())
    return out, hidden
