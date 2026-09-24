"""The finance dashboard: one backend answer for the whole screen. The frontend only displays these figures; it
never adds, subtracts or averages money itself.

Every number is produced by the same service that powers the matching report (profit and loss, cash flow,
receivables, payables, tax, expenses), so the dashboard and the reports can never disagree. A comparison period
is the period of equal length immediately before the chosen one. Where a figure cannot be known (profit with an
unknown cost, a change against a zero base) it is null, never zero.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import (
    cashflow_service,
    expense_service,
    finance_alert_service,
    payables_service,
    pnl_service,
    receivables_service,
    tax_service,
)

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Change:
    current: Decimal | None
    previous: Decimal | None
    change_pct: Decimal | None  # null when either side is unknown or the base is zero


def _change(current: Decimal | None, previous: Decimal | None) -> Change:
    pct = None
    if current is not None and previous is not None and previous != 0:
        pct = ((current - previous) / abs(previous) * 100).quantize(Decimal("0.1"))
    return Change(current, previous, pct)


@dataclass(frozen=True)
class CategorySlice:
    category_id: int
    name: str
    amount: Decimal


@dataclass(frozen=True)
class MethodSlice:
    payment_method: str
    inflow: Decimal
    outflow: Decimal


@dataclass(frozen=True)
class FinanceDashboard:
    date_from: date
    date_to: date
    comparison_from: date | None
    comparison_to: date | None
    pnl: pnl_service.ProfitAndLoss
    cash_flow: cashflow_service.CashFlowReport
    receivables_total: Decimal
    payables_total: Decimal
    customer_outstanding: Decimal
    supplier_outstanding: Decimal
    receivables_aging: dict[str, Decimal]
    payables_aging: dict[str, Decimal]
    tax_status: str
    tax_collected: Decimal | None
    tax_paid: Decimal | None
    net_tax: Decimal | None
    revenue_change: Change | None
    gross_profit_change: Change | None
    net_profit_change: Change | None
    expenses_change: Change | None
    net_cash_flow_change: Change | None
    revenue_trend: list[pnl_service.TrendPoint]
    cash_flow_trend: list[cashflow_service.FlowPoint]
    expense_categories: list[CategorySlice]
    payment_method_mix: list[MethodSlice]
    alert_count: int
    granularity: str
    notes: list[str] = field(default_factory=list)


def _granularity(start: date, end: date) -> str:
    span = (end - start).days + 1
    return "day" if span <= 31 else "week" if span <= 140 else "month"


def build(
    session: Session,
    shop_id: int,
    date_from: date,
    date_to: date,
    *,
    compare: bool = True,
    today: date | None = None,
) -> FinanceDashboard:
    pnl = pnl_service.compute(session, shop_id, date_from, date_to)
    flow = cashflow_service.compute(session, shop_id, date_from, date_to)
    rec = receivables_service.compute(session, shop_id, date_to)
    pay = payables_service.compute(session, shop_id, date_to)
    tax = tax_service.summary(session, shop_id, date_from, date_to)
    granularity = _granularity(date_from, date_to)

    prev_from = prev_to = None
    revenue_c = gross_c = net_c = exp_c = cash_c = None
    if compare:
        span = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=span - 1)
        prev = pnl_service.compute(session, shop_id, prev_from, prev_to)
        prev_flow = cashflow_service.compute(session, shop_id, prev_from, prev_to)
        revenue_c = _change(pnl.revenue, prev.revenue)
        gross_c = _change(pnl.gross_profit, prev.gross_profit)
        net_c = _change(pnl.net_profit, prev.net_profit)
        exp_c = _change(pnl.operating_expenses, prev.operating_expenses)
        cash_c = _change(flow.net, prev_flow.net)

    categories = [
        CategorySlice(cid, name, amt)
        for cid, name, amt in expense_service.expenses_by_category(session, shop_id, date_from, date_to)
    ]
    mix = sorted(
        (MethodSlice(m, f.inflow, f.outflow) for m, f in flow.by_method.items()),
        key=lambda s: -(s.inflow + s.outflow),
    )
    alerts = finance_alert_service.compute(session, shop_id, today or date_to)
    notes = list(pnl.notes)
    if tax.status != "CONFIGURED":
        notes.append("Tax is not configured, so no tax figures are shown.")
    return FinanceDashboard(
        date_from, date_to, prev_from, prev_to, pnl, flow, rec.total_receivables, pay.total_payable,
        rec.total_receivables, pay.total_payable, rec.aging, pay.aging, tax.status, tax.tax_collected, tax.tax_paid,
        tax.net_tax, revenue_c, gross_c, net_c, exp_c, cash_c, pnl_service.trend(session, shop_id, date_from, date_to, granularity),
        cashflow_service.trend(session, shop_id, date_from, date_to, granularity), categories, mix, len(alerts),
        granularity, notes,
    )  # fmt: skip
