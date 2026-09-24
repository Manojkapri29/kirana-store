"""Finance analytics: every number is a Phase 15 finance figure (profit and loss, cash flow, receivables from khata, payables,
posted expenses, tax reporting foundation), presented for a period with an optional comparison. Nothing is recomputed here, so
each figure stays traceable to the financial ledger. When cost is unknown, profit is "Profit Not Available (Insufficient Cost
Data)", never a zero.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.reporting.filters import ReportFilters, ReportTable
from app.reporting.sales import _pct, _table
from app.services import (
    cashflow_service,
    expense_service,
    payables_service,
    pnl_service,
    receivables_service,
    tax_service,
)
from app.services.errors import InvalidInputError

ZERO = Decimal("0.00")
NO_PROFIT = "Profit Not Available (Insufficient Cost Data)"


def _pnl_dict(p: pnl_service.ProfitAndLoss) -> dict:
    return {
        "revenue": p.revenue,
        "cogs": p.cogs,
        "gross_profit": p.gross_profit,
        "gross_margin_pct": p.gross_margin_pct,
        "operating_expenses": p.operating_expenses,
        "net_profit": p.net_profit,
        "status": p.status,
        "status_note": p.status_note,
    }


def summary(session: Session, shop_id: int, f: ReportFilters) -> dict:
    cur = pnl_service.compute(session, shop_id, f.period.start, f.period.end)
    cf = cashflow_service.compute(session, shop_id, f.period.start, f.period.end)
    out = {
        "period": f.period,
        "comparison": f.comparison,
        "pnl": _pnl_dict(cur),
        "profit_message": None if cur.gross_profit is not None else NO_PROFIT,
        "cash_inflow": cf.inflow,
        "cash_outflow": cf.outflow,
        "net_cash_flow": cf.net,
        "receivables": receivables_service.compute(session, shop_id, f.period.end).total_receivables,
        "payables": payables_service.compute(session, shop_id, f.period.end).total_payable,
        "notes": list(cur.notes),
    }
    if f.comparison:
        prev = pnl_service.compute(session, shop_id, f.comparison.start, f.comparison.end)
        pcf = cashflow_service.compute(session, shop_id, f.comparison.start, f.comparison.end)
        out["previous"] = {"pnl": _pnl_dict(prev), "net_cash_flow": pcf.net}
    return out


MAX_BUCKETS = (
    62  # each bucket is a full profit-and-loss and cash-flow calculation, so the count of buckets is bounded
)


def check_buckets(f: ReportFilters, bucket: str) -> None:
    """Refuse a trend that would need more than MAX_BUCKETS calculations (a daily view of several years, say)."""
    per = {"day": 1, "week": 7, "month": 28}.get(bucket)
    if per is None:
        raise InvalidInputError("Choose day, week or month.", field="bucket")
    if -(-f.period.days // per) > MAX_BUCKETS:
        raise InvalidInputError(
            f"That is too many {bucket}s to work out at once (the limit is {MAX_BUCKETS}). Choose a shorter period or a longer step.",
            field="bucket",
        )


def auto_bucket(f: ReportFilters) -> str:
    """The finest step that keeps a trend within MAX_BUCKETS: days for two months or less, weeks up to a year and a bit, else months."""
    return "day" if f.period.days <= MAX_BUCKETS else "week" if f.period.days <= 7 * MAX_BUCKETS else "month"


def trend(session: Session, shop_id: int, f: ReportFilters, bucket: str = "month") -> ReportTable:
    check_buckets(f, bucket)
    points = pnl_service.trend(session, shop_id, f.period.start, f.period.end, bucket)
    flow = {
        p.period_start: p
        for p in cashflow_service.trend(session, shop_id, f.period.start, f.period.end, bucket)
    }
    rows = [
        {
            "period": p.period_start.isoformat(),
            "revenue": p.revenue,
            "cogs": p.cogs,
            "gross_profit": p.gross_profit,
            "operating_expenses": p.operating_expenses,
            "net_profit": p.net_profit,
            "net_cash_flow": flow[p.period_start].net if p.period_start in flow else None,
        }
        for p in points
    ]
    cols = [
        ("period", "Period start", "text"),
        ("revenue", "Revenue", "money"),
        ("cogs", "COGS", "money"),
        ("gross_profit", "Gross profit", "money"),
        ("operating_expenses", "Expenses", "money"),
        ("net_profit", "Net profit", "money"),
        ("net_cash_flow", "Net cash flow", "money"),
    ]
    return _table(
        f,
        f"Finance by {bucket}",
        cols,
        rows,
        set(),
        "Profit and loss and the financial ledger",
        ["A blank profit cell is 'Profit Not Available (Insufficient Cost Data)', not zero."],
    )


def expenses(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Posted expenses by category, with the change against the comparison period where there is one."""
    cur = {
        cid: (name, amt)
        for cid, name, amt in expense_service.expenses_by_category(
            session, shop_id, f.period.start, f.period.end
        )
    }
    prev = (
        {
            cid: (name, amt)
            for cid, name, amt in expense_service.expenses_by_category(
                session, shop_id, f.comparison.start, f.comparison.end
            )
        }
        if f.comparison
        else {}
    )
    rows = []
    for cid in set(cur) | set(prev):
        name = (cur.get(cid) or prev[cid])[0]
        c, p = (cur[cid][1] if cid in cur else ZERO), (prev[cid][1] if cid in prev else None)
        rows.append(
            {
                "category_id": cid,
                "category": name,
                "amount": c,
                "previous_amount": p,
                "change": (c - p) if p is not None else None,
                "change_pct": _pct(c - p, p) if p not in (None, ZERO) else None,
            }
        )
    rows.sort(key=lambda r: (-r["amount"], r["category"].casefold()))
    cols = [
        ("category", "Category", "text"),
        ("amount", "Posted expenses", "money"),
        ("previous_amount", "Previous period", "money"),
        ("change", "Change", "money"),
        ("change_pct", "Change %", "percent"),
    ]
    return _table(
        f,
        "Expenses by category",
        cols,
        rows,
        set(),
        "The finance ledger (posted expenses less reversals)",
        [
            "Only posted expenses count. A blank change % means the previous amount was zero or there is no comparison."
        ],
    )


def expense_trend(session: Session, shop_id: int, f: ReportFilters, bucket: str = "month") -> ReportTable:
    check_buckets(f, bucket)
    rows = [
        {"period": p.period_start.isoformat(), "operating_expenses": p.operating_expenses}
        for p in pnl_service.trend(session, shop_id, f.period.start, f.period.end, bucket)
    ]
    return _table(
        f,
        f"Expenses by {bucket}",
        [("period", "Period start", "text"), ("operating_expenses", "Posted expenses", "money")],
        rows,
        set(),
        "The finance ledger",
    )


def payment_mix(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    r = cashflow_service.compute(session, shop_id, f.period.start, f.period.end)
    rows = [
        {"payment_method": m, "inflow": fl.inflow, "outflow": fl.outflow, "net": fl.net}
        for m, fl in sorted(r.by_method.items())
    ]
    cols = [
        ("payment_method", "Payment method", "text"),
        ("inflow", "Money in", "money"),
        ("outflow", "Money out", "money"),
        ("net", "Net", "money"),
    ]
    return _table(
        f, "Payment method mix", cols, rows, set(), "The financial ledger (settled amounts)", r.notes
    )


def aging(session: Session, shop_id: int, f: ReportFilters, which: str) -> ReportTable:
    rep = (
        receivables_service.compute(session, shop_id, f.period.end)
        if which == "receivables"
        else payables_service.compute(session, shop_id, f.period.end)
    )
    rows = [{"bucket": b, "amount": v} for b, v in rep.aging.items()]
    return _table(
        f,
        f"{which.title()} ageing",
        [("bucket", "Age (days)", "text"), ("amount", "Amount", "money")],
        rows,
        set(),
        "Khata" if which == "receivables" else "Purchases, returns and supplier payments",
        [rep.methodology],
    )


def tax(session: Session, shop_id: int, f: ReportFilters) -> dict:
    t = tax_service.summary(session, shop_id, f.period.start, f.period.end)
    return {
        "period": f.period,
        "status": t.status,
        "tax_collected": t.tax_collected,
        "tax_paid": t.tax_paid,
        "net_tax": t.net_tax,
        "quick_sales_amount": t.quick_sales_amount,
        "disclaimer": t.disclaimer,
        "notes": t.notes,
    }
