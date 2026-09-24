"""Profit and loss: revenue, cost of goods, gross profit, operating expenses, net profit.

    Revenue        = Detailed Sales + Quick Sales - Sales Returns          (posted documents, by their date)
    COGS           = the cost snapshots the costing service stored on each detailed-sale line, less the cost of
                     goods that came back on returns
    Gross profit   = Revenue - COGS
    Operating exp. = posted expenses (a voided one is netted out by its reversal); drafts, submitted, approved
                     and rejected expenses never count
    Net profit     = Gross profit - Operating expenses
    Gross margin % = Gross profit / Revenue x 100

HONESTY RULES. Cost is only known for Detailed Sale lines. A Quick Sale is a total with no product lines, so it
has revenue but NO cost, and no product-level profit is ever invented for it. A detailed sale with any line of
unknown cost is left out of the costed figures (an unknown cost is never treated as zero). So:

* `status` is ACTUAL only when every rupee of revenue has a known cost; then all profit figures are present.
* Otherwise `status` is NOT_AVAILABLE ("Insufficient Cost Data"): COGS, gross profit, net profit and margin are
  null. What CAN be stated honestly is given separately and labelled: `costed_sales`, the profit on just the
  detailed sales whose cost is fully known (status PARTIAL, with the share of revenue it covers).

Online orders: this system has no online-order module. If one existed and produced detailed sales, those sales
would already be inside "Detailed Sales" and must not be added again; nothing is added here.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy.orm import Session

from app.models.enums import FinanceEventType, FlowDirection
from app.services import expense_service, finance_ledger_service, sales_report_service

ZERO = Decimal("0.00")
ACTUAL = "ACTUAL"
PARTIAL = "PARTIAL"
NOT_AVAILABLE = "NOT_AVAILABLE"
INSUFFICIENT_COST_DATA = "Insufficient Cost Data"
PROFIT_NOT_AVAILABLE = "Profit Not Available"


@dataclass(frozen=True)
class CostedSales:
    """Profit on the detailed sales whose cost is fully known: a subset, never the whole business."""

    status: str  # PARTIAL, or NOT_AVAILABLE when there is no costed sale
    revenue: Decimal | None
    cogs: Decimal | None
    gross_profit: Decimal | None
    coverage_pct: Decimal | None  # of net revenue


@dataclass(frozen=True)
class ProfitAndLoss:
    date_from: date
    date_to: date
    detailed_sales: Decimal
    quick_sales: Decimal
    online_sales: Decimal
    sales_returns: Decimal
    revenue: Decimal
    cogs: Decimal | None
    gross_profit: Decimal | None
    operating_expenses: Decimal
    net_profit: Decimal | None
    gross_margin_pct: Decimal | None
    status: str
    status_note: str
    costed_sales: CostedSales
    other_income: Decimal  # informational: NOT part of net profit as defined above
    detailed_sales_without_cost: int
    returns_without_cost: int
    quick_sales_have_no_cost: bool
    cogs_kind: str = ACTUAL  # ACTUAL, or NOT_AVAILABLE
    notes: list[str] = field(default_factory=list)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return (numerator / denominator * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def compute(session: Session, shop_id: int, date_from: date, date_to: date) -> ProfitAndLoss:
    summary = sales_report_service.sales_summary(session, shop_id, date_from, date_to)
    detailed = summary.detailed.net
    quick = summary.quick.net
    returns = summary.returns_total
    revenue = detailed + quick - returns
    expenses = expense_service.posted_expense_total(session, shop_id, date_from, date_to)

    other_income = ZERO
    for row in finance_ledger_service.movements(session, shop_id, date_from, date_to):
        if row.event_type is FinanceEventType.OTHER_INCOME:
            other_income += row.settled_amount if row.direction is FlowDirection.IN else -row.settled_amount

    notes: list[str] = []
    all_costed = (
        summary.quick.net == 0
        and summary.detailed_sales_without_cost == 0
        and summary.returns_without_cost == 0
    )
    if summary.quick.net > 0:
        notes.append("Quick Sales add to revenue but carry no product cost, so they have no profit figure.")
    if summary.detailed_sales_without_cost:
        notes.append(
            f"{summary.detailed_sales_without_cost} detailed sale(s) have a line with an unknown cost."
        )
    if summary.returns_without_cost:
        notes.append(f"{summary.returns_without_cost} return(s) have a returned item with an unknown cost.")

    costed_gross = summary.detailed_gross_profit
    if costed_gross is not None:
        costed = CostedSales(
            PARTIAL,
            summary.costed_net,
            summary.costed_cogs,
            costed_gross,
            _pct(summary.costed_net or ZERO, revenue),
        )
    else:
        costed = CostedSales(NOT_AVAILABLE, None, None, None, None)

    if all_costed and revenue == 0 and detailed == 0:
        # Nothing was sold: there is no cost of goods sold to report, and that is a fact, not a gap.
        cogs, gross, status, note = ZERO, ZERO, ACTUAL, "No sales in this period."
    elif all_costed and costed_gross is not None:
        gross = costed_gross
        cogs = revenue - gross
        status, note = ACTUAL, "Every sale in this period has a known cost."
    else:
        cogs = gross = None
        status, note = NOT_AVAILABLE, INSUFFICIENT_COST_DATA

    net = None if gross is None else gross - expenses
    margin = None if gross is None else _pct(gross, revenue)
    return ProfitAndLoss(
        date_from=date_from, date_to=date_to, detailed_sales=detailed, quick_sales=quick, online_sales=ZERO,
        sales_returns=returns, revenue=revenue, cogs=cogs, gross_profit=gross, operating_expenses=expenses,
        net_profit=net, gross_margin_pct=margin, status=status, status_note=note, costed_sales=costed,
        other_income=other_income, detailed_sales_without_cost=summary.detailed_sales_without_cost,
        returns_without_cost=summary.returns_without_cost, quick_sales_have_no_cost=summary.quick.net > 0,
        cogs_kind=ACTUAL if cogs is not None else NOT_AVAILABLE,
        notes=notes + ([PROFIT_NOT_AVAILABLE] if gross is None else []),
    )  # fmt: skip


@dataclass(frozen=True)
class TrendPoint:
    period_start: date
    period_end: date
    revenue: Decimal
    cogs: Decimal | None
    gross_profit: Decimal | None
    operating_expenses: Decimal
    net_profit: Decimal | None


def _buckets(start: date, end: date, granularity: str) -> list[tuple[date, date]]:
    out, cursor = [], start
    while cursor <= end:
        if granularity == "day":
            stop = cursor
        elif granularity == "week":
            stop = min(cursor + timedelta(days=6 - cursor.weekday()), end)
        else:  # month
            first_next = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            stop = min(first_next - timedelta(days=1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def trend(
    session: Session, shop_id: int, date_from: date, date_to: date, granularity: str = "day"
) -> list[TrendPoint]:
    """One P&L per day/week/month. A bucket whose cost is unknown has null profit, never zero."""
    if granularity not in ("day", "week", "month"):
        raise ValueError("granularity must be day, week or month")
    points = []
    for a, b in _buckets(date_from, date_to, granularity):
        p = compute(session, shop_id, a, b)
        points.append(TrendPoint(a, b, p.revenue, p.cogs, p.gross_profit, p.operating_expenses, p.net_profit))
    return points
