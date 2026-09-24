"""Read-only business-intelligence tools for the AI assistant (Phase 16).

Every tool calls the same reporting function the analytics screens and exports use (`app.reporting`), so the assistant can only
repeat what those report; it never recomputes, estimates or predicts. Each answer states the PERIOD, the METRICS, the SOURCE, the
CALCULATION and the LIMITATIONS. No tool writes anything or accepts SQL: arguments are a period, a fixed `view` name from a
short list, and (for saved reports) an id. A KPI or report the caller may not read is refused or hidden, never zeroed.
The assistant's words are templates over these figures; a language model never writes a number.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models import SavedReport
from app.reporting import builder, cohorts, crosslinks, executive, kpis
from app.reporting import customers as customer_reports
from app.reporting import finance as finance_reports
from app.reporting import inventory as inventory_reports
from app.reporting import sales as sales_reports
from app.reporting import suppliers as supplier_reports
from app.reporting.filters import CompareMode, ReportFilters, ReportTable, build_filters
from app.services import ai_format as fmt
from app.services.ai_answer import ANSWERED, NO_DATA, NOT_AVAILABLE, Answer, Figure, Table
from app.services.errors import InvalidInputError, NotFoundError

MAX_TABLE_ROWS = 15
NA = "Not Available"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BiArgs(_Args):
    period: str | None = Field(
        default=None, max_length=80, description='A phrase such as "this month" or "last quarter".'
    )
    date_from: date | None = None
    date_to: date | None = None
    compare: bool = Field(default=True, description="Also compare with the previous comparable period.")


class KpiArgs(BiArgs):
    kpi: str = Field(max_length=60, description="A KPI name or key, for example revenue or gross_margin.")


class SalesViewArgs(BiArgs):
    view: Literal["summary", "trend", "products", "categories", "channels", "payment_methods"] = "summary"


class InventoryViewArgs(BiArgs):
    view: Literal["summary", "stock", "turnover", "fast", "slow", "dead", "reorder", "stock_outs"] = "summary"


class CustomerViewArgs(BiArgs):
    view: Literal["overview", "segments", "loyalty"] = "overview"


class SupplierViewArgs(BiArgs):
    view: Literal["overview", "spend", "returns"] = "overview"


class FinanceViewArgs(BiArgs):
    view: Literal["summary", "trend", "expenses", "payment_mix"] = "summary"


class CohortArgs(BiArgs):
    months: int = Field(default=6, ge=1, le=24)


class SavedReportArgs(BiArgs):
    report_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, max_length=80)


# --- helpers ------------------------------------------------------------------------------------------------------


def _t(tc: Any, en: str, hi: str | None = None) -> str:
    return tc.t(en, hi)


def _filters(tc: Any, args: BiArgs, default: str = "this month") -> ReportFilters:
    p = tc.period(args, default)
    return build_filters(
        tc.today,
        date_from=p.start,
        date_to=p.end,
        compare=CompareMode.PREVIOUS_PERIOD if args.compare else CompareMode.NONE,
        limit=200,
    )


def _period(f: ReportFilters) -> dict[str, str]:
    return {"from": f.period.start.isoformat(), "to": f.period.end.isoformat(), "label": f.period.label}


def _cell(value: object, kind: str) -> str:
    if value is None:
        return NA
    if isinstance(value, Decimal):
        if kind == "money":
            return fmt.money(value)
        if kind == "percent":
            return f"{value:.2f}%"
        return fmt.quantity(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _value(v: kpis.Value, unit: str) -> str:
    if v.amount is None:
        return NA if v.availability is kpis.Availability.NOT_AVAILABLE else "Insufficient Data"
    if unit == "money":
        return fmt.money(v.amount)
    if unit == "percent":
        return f"{v.amount}%"
    return fmt.quantity(v.amount)


def _change(r: kpis.KpiResult) -> str | None:
    if r.change is None:
        return None
    if r.change.percent is not None:
        return f"{r.change.percent:+}%"
    if r.change.absolute is not None and r.definition.unit == "percent":
        return f"{r.change.absolute:+} points"
    return r.change.note


def _table_answer(
    tc: Any,
    tool: str,
    table: ReportTable,
    f: ReportFilters,
    message: str,
    calculation: str,
    status: str = ANSWERED,
) -> Answer:
    shown = table.rows[:MAX_TABLE_ROWS]
    notes = [_t(tc, f"Calculation: {calculation}"), *table.notes]
    if table.total > len(shown):
        notes.append(_t(tc, f"Showing {len(shown)} of {table.total}. Export the report for every row."))
    return Answer(
        status if shown else NO_DATA, tool, table.title, message if shown else _t(tc, "There is nothing to report for this period."),
        table=Table([label for _k, label, _kind in table.columns], [[_cell(r.get(k), kind) for k, _l, kind in table.columns] for r in shown]) if shown else None,
        sources=[table.source or "Analytics reports"], period=_period(f), notes=notes,
    )  # fmt: skip


def _dict_answer(
    tc: Any,
    tool: str,
    title: str,
    message: str,
    figures: list[Figure],
    f: ReportFilters,
    source: str,
    calculation: str,
    notes: list[str],
) -> Answer:
    return Answer(
        ANSWERED,
        tool,
        title,
        message,
        figures,
        sources=[source],
        period=_period(f),
        notes=[_t(tc, f"Calculation: {calculation}"), *notes],
    )


def _money(v: Decimal | None) -> str:
    return fmt.money(v)


# --- tools --------------------------------------------------------------------------------------------------------


def get_kpi(tc: Any, args: KpiArgs) -> Answer:
    f = _filters(tc, args)
    wanted = args.kpi.strip().casefold().replace(" ", "_")
    match = next(
        (
            k
            for k, (d, _fn) in kpis.KPIS.items()
            if k == wanted or d.name.casefold().replace(" ", "_") == wanted
        ),
        None,
    )
    granted = set(tc.ctx.granted)
    visible = [k for k, (d, _fn) in kpis.KPIS.items() if d.permission in granted]
    if match is None or match not in visible:
        return Answer(
            NO_DATA,
            "get_kpi",
            _t(tc, "KPI"),
            _t(tc, "I don't have a KPI by that name. Available: ") + ", ".join(visible),
            period=_period(f),
        )
    (r,), _hidden = kpis.compute(tc.session, tc.ctx.shop_id, f, tc.today, [match], granted)
    d = r.definition
    figures = [Figure(d.name, _value(r.current, d.unit))]
    if r.previous is not None:
        figures.append(Figure(_t(tc, "Previous period"), _value(r.previous, d.unit), _change(r)))
    status = ANSWERED if r.current.amount is not None else NOT_AVAILABLE
    message = (
        f"{d.name} for {f.period.label}: {_value(r.current, d.unit)}."
        if r.current.amount is not None
        else f"{d.name} is not available: {r.current.reason}"
    )
    notes = [
        _t(tc, f"Calculation: {d.formula}"),
        _t(tc, f"Definition: {d.description}"),
        _t(tc, f"Limitation: {d.limitations}"),
    ]
    return Answer(
        status, "get_kpi", d.name, message, figures, sources=[d.source], period=_period(f), notes=notes
    )


def get_executive_dashboard(tc: Any, args: BiArgs) -> Answer:
    f = _filters(tc, args)
    dash = executive.build(tc.session, tc.ctx.shop_id, f, tc.today, set(tc.ctx.granted))
    rows = []
    for section in dash.sections:
        for r in section.kpis:
            rows.append(
                [section.title, r.definition.name, _value(r.current, r.definition.unit), _change(r) or "—"]
            )
    return Answer(
        ANSWERED if rows else NO_DATA, "get_executive_dashboard", _t(tc, "Executive dashboard"),
        _t(tc, f"Executive dashboard for {f.period.label}: {len(rows)} figures across {len(dash.sections)} sections."),
        table=Table([_t(tc, "Section"), _t(tc, "KPI"), _t(tc, "Value"), _t(tc, "Change")], rows) if rows else None,
        sources=["The KPI framework (same values as the dashboard and exports)"], period=_period(f),
        notes=[_t(tc, "Calculation: each KPI has its own formula; ask for one KPI to see it."), *dash.notes],
    )  # fmt: skip


def get_sales_analytics(tc: Any, args: SalesViewArgs) -> Answer:
    f = _filters(tc, args)
    s, sid = tc.session, tc.ctx.shop_id
    if args.view == "summary":
        d = sales_reports.summary(s, sid, f)
        figs = [
            Figure(_t(tc, "Revenue (detailed + quick)"), _money(d["combined_revenue"])),
            Figure(_t(tc, "Detailed sales"), _money(d["detailed_revenue"])),
            Figure(_t(tc, "Quick sales"), _money(d["quick_revenue"])),
            Figure(_t(tc, "Sales returns"), _money(d["sales_returns"])),
            Figure(_t(tc, "Transactions"), str(d["transactions"])),
            Figure(_t(tc, "Average transaction value"), _money(d["average_transaction_value"])),
            Figure(_t(tc, "Online sales"), NA),
        ]
        if f.comparison:
            prev = sales_reports.summary(s, sid, ReportFilters(period=f.comparison, comparison=None))[
                "combined_revenue"
            ]
            cur = d["combined_revenue"]
            figs.append(
                Figure(
                    _t(tc, "Previous period revenue"),
                    _money(prev),
                    f"{fmt.percent_change(cur, prev)}%"
                    if fmt.percent_change(cur, prev) is not None
                    else _t(tc, "Insufficient comparison data"),
                )
            )
        return _dict_answer(
            tc,
            "get_sales_analytics",
            _t(tc, "Sales analytics"),
            _t(tc, f"Sales for {f.period.label}."),
            figs,
            f,
            "Posted detailed sales and quick sales",
            "Detailed sales + Quick Sales, by posted date; returns are shown separately.",
            d["notes"],
        )
    table = {
        "trend": lambda: sales_reports.trend(s, sid, f, "month"),
        "products": lambda: sales_reports.products(s, sid, f, "top"),
        "categories": lambda: sales_reports.categories(s, sid, f),
        "channels": lambda: sales_reports.channels(s, sid, f),
        "payment_methods": lambda: sales_reports.payment_methods(s, sid, f),
    }[args.view]()
    return _table_answer(
        tc,
        "get_sales_analytics",
        table,
        f,
        _t(tc, f"{table.title} for {f.period.label}."),
        "posted sales by the chosen view; Quick Sales have no product detail.",
    )


def get_inventory_analytics(tc: Any, args: InventoryViewArgs) -> Answer:
    f = _filters(tc, args)
    s, sid = tc.session, tc.ctx.shop_id
    if args.view == "summary":
        table = inventory_reports.category_summary(s, sid, f)
    else:
        table = {
            "stock": lambda: inventory_reports.stock(s, sid, f),
            "turnover": lambda: inventory_reports.turnover(s, sid, f),
            "fast": lambda: inventory_reports.movers(s, sid, f, "fast"),
            "slow": lambda: inventory_reports.movers(s, sid, f, "slow"),
            "dead": lambda: inventory_reports.movers(s, sid, f, "dead"),
            "reorder": lambda: inventory_reports.reorder(s, sid, f),
            "stock_outs": lambda: inventory_reports.stock_outs(s, sid, f),
        }[args.view]()
    return _table_answer(
        tc,
        "get_inventory_analytics",
        table,
        f,
        _t(tc, f"{table.title}: {table.total} row(s)."),
        "read from the inventory transaction ledger; value = stock x average cost, and stock without a known cost is left out, never counted as zero.",
    )


def get_customer_analytics(tc: Any, args: CustomerViewArgs) -> Answer:
    f = _filters(tc, args)
    s, sid = tc.session, tc.ctx.shop_id
    if args.view == "overview":
        d = customer_reports.overview(s, sid, f, tc.today)
        figs = [
            Figure(_t(tc, "Purchasing customers"), str(d["purchasing_customers"])),
            Figure(_t(tc, "New customers"), str(d["new_customers"])),
            Figure(_t(tc, "Returning customers"), str(d["returning_customers"])),
            Figure(_t(tc, "Average customer value"), _money(d["average_customer_value"])),
            Figure(
                _t(tc, "Purchase frequency"),
                str(d["purchase_frequency"]) if d["purchase_frequency"] is not None else NA,
            ),
            Figure(_t(tc, "Online customer activity"), NA),
        ]
        return _dict_answer(
            tc,
            "get_customer_analytics",
            _t(tc, "Customer analytics"),
            _t(tc, f"Customers for {f.period.label}."),
            figs,
            f,
            "Posted sales that name a customer; CRM",
            "counts of identified customers; a customer is new when their first purchase ever falls in the period.",
            d["notes"],
        )
    table = (
        customer_reports.segments(s, sid, f, tc.today)
        if args.view == "segments"
        else customer_reports.loyalty(s, sid, f)
    )
    return _table_answer(
        tc,
        "get_customer_analytics",
        table,
        f,
        _t(tc, f"{table.title} for {f.period.label}."),
        "identified purchases grouped by the chosen view. Only recency, frequency, spend and credit are used; nothing sensitive is inferred.",
    )


def get_supplier_analytics(tc: Any, args: SupplierViewArgs) -> Answer:
    f = _filters(tc, args)
    s, sid = tc.session, tc.ctx.shop_id
    if args.view == "overview":
        d = supplier_reports.overview(s, sid, f)
        figs = [
            Figure(_t(tc, "Net purchase value"), _money(d["net_purchase_value"])),
            Figure(_t(tc, "Purchases"), str(d["purchase_count"])),
            Figure(_t(tc, "Suppliers with spend"), str(d["suppliers"])),
            Figure(_t(tc, "Largest supplier"), d["top_supplier"] or NA),
            Figure(
                _t(tc, "Largest supplier's share"),
                f"{d['top_supplier_share_pct']}%" if d["top_supplier_share_pct"] is not None else NA,
            ),
        ]
        return _dict_answer(
            tc,
            "get_supplier_analytics",
            _t(tc, "Supplier analytics"),
            _t(tc, f"Purchases for {f.period.label}."),
            figs,
            f,
            "Posted purchases and purchase returns",
            "net spend = posted purchases - posted purchase returns; share = supplier net spend / total net spend.",
            d["notes"],
        )
    table = (
        supplier_reports.suppliers(s, sid, f) if args.view == "spend" else supplier_reports.returns(s, sid, f)
    )
    return _table_answer(
        tc,
        "get_supplier_analytics",
        table,
        f,
        _t(tc, f"{table.title} for {f.period.label}."),
        "posted purchases and returns per supplier. Suppliers are not ranked best or worst.",
    )


def get_finance_analytics(tc: Any, args: FinanceViewArgs) -> Answer:
    f = _filters(tc, args)
    s, sid = tc.session, tc.ctx.shop_id
    if args.view == "summary":
        d = finance_reports.summary(s, sid, f)
        p = d["pnl"]
        figs = [
            Figure(_t(tc, "Revenue"), _money(p["revenue"])),
            Figure(_t(tc, "Cost of goods sold"), _money(p["cogs"])),
            Figure(_t(tc, "Gross profit"), _money(p["gross_profit"])),
            Figure(_t(tc, "Posted expenses"), _money(p["operating_expenses"])),
            Figure(_t(tc, "Net profit"), _money(p["net_profit"])),
            Figure(_t(tc, "Net cash flow"), _money(d["net_cash_flow"])),
            Figure(_t(tc, "Receivables"), _money(d["receivables"])),
            Figure(_t(tc, "Payables"), _money(d["payables"])),
        ]
        msg = (
            _t(tc, f"Finance for {f.period.label}.")
            if p["gross_profit"] is not None
            else _t(
                tc,
                f"Profit Not Available (Insufficient Cost Data) for {f.period.label}; revenue and expenses are shown.",
            )
        )
        ans = _dict_answer(
            tc,
            "get_finance_analytics",
            _t(tc, "Finance analytics"),
            msg,
            figs,
            f,
            "Profit and loss and the financial ledger",
            "Gross profit = revenue - cost of goods sold; Net profit = gross profit - posted expenses.",
            d["notes"],
        )
        if p["gross_profit"] is None:
            ans.status = NOT_AVAILABLE
        return ans
    table = {
        "trend": lambda: finance_reports.trend(s, sid, f, "month"),
        "expenses": lambda: finance_reports.expenses(s, sid, f),
        "payment_mix": lambda: finance_reports.payment_mix(s, sid, f),
    }[args.view]()
    return _table_answer(
        tc,
        "get_finance_analytics",
        table,
        f,
        _t(tc, f"{table.title} for {f.period.label}."),
        "figures read from the financial ledger and profit and loss; a blank profit is Profit Not Available.",
    )


def get_cohort_report(tc: Any, args: CohortArgs) -> Answer:
    f = _filters(tc, args, "this year")
    table = cohorts.cohorts(
        tc.session,
        tc.ctx.shop_id,
        ReportFilters(period=f.period, comparison=None, limit=200),
        tc.today,
        args.months,
    )
    return _table_answer(
        tc,
        "get_cohort_report",
        table,
        f,
        _t(tc, f"Cohort retention: {table.total} cohort-month row(s)."),
        "customers grouped by the month of their first purchase; retention = customers who bought again in month N / cohort size. Nothing is extrapolated.",
    )


def get_cross_module_insights(tc: Any, args: BiArgs) -> Answer:
    f = _filters(tc, args)
    found, hidden = crosslinks.build(tc.session, tc.ctx.shop_id, f, tc.today, set(tc.ctx.granted))
    rows = [[i.title, i.availability, i.statement] for i in found]
    notes = [
        _t(tc, "Calculation: each line places facts from two modules side by side for the same period."),
        crosslinks.CAUTION,
    ]
    if hidden:
        notes.append(
            _t(tc, f"{len(hidden)} insight(s) are hidden because your role cannot read the data they use.")
        )
    return Answer(
        ANSWERED if rows else NO_DATA, "get_cross_module_insights", _t(tc, "Observed together"),
        _t(tc, f"{len(rows)} observation(s) for {f.period.label}. These are things seen together, not causes."),
        table=Table([_t(tc, "Observation"), _t(tc, "Availability"), _t(tc, "What was seen")], rows) if rows else None,
        sources=["Sales, inventory, purchases, CRM and finance reports"], period=_period(f), notes=notes,
    )  # fmt: skip


def get_saved_report(tc: Any, args: SavedReportArgs) -> Answer:
    if args.report_id is None and not args.name:
        raise InvalidInputError("Give the saved report's id or name.", field="report_id")
    q = select(SavedReport).where(SavedReport.shop_id == tc.ctx.shop_id, SavedReport.is_archived.is_(False))
    q = (
        q.where(SavedReport.id == args.report_id)
        if args.report_id
        else q.where(SavedReport.name == args.name)
    )
    saved = tc.session.scalar(q)
    if saved is None:
        raise NotFoundError("Saved report not found")
    f = _filters(tc, args)
    query = builder.validate(
        saved.definition, saved.dataset, set(tc.ctx.granted)
    )  # the caller's own permissions, right now
    table = builder.run(tc.session, tc.ctx.shop_id, query, f, tc.today)
    table.title = saved.name
    return _table_answer(
        tc,
        "get_saved_report",
        table,
        f,
        _t(tc, f"Saved report '{saved.name}' for {f.period.label}."),
        f"the saved definition over the {saved.dataset} dataset, filtered, grouped and aggregated exactly as saved.",
    )


# name -> (description, args model, function, primary permission, extra permissions). The extras are the data permissions the
# matching analytics endpoint also requires, so the assistant never shows what the screen would refuse.
BI_TOOLS: dict[str, tuple[str, type[_Args], Any, str, tuple[str, ...]]] = {
    "get_kpi": (
        "One KPI for a period with its formula, source and change against the previous period.",
        KpiArgs,
        get_kpi,
        "ANALYTICS_VIEW",
        (),
    ),
    "get_executive_dashboard": (
        "The executive dashboard: every KPI you may see, by section, with change.",
        BiArgs,
        get_executive_dashboard,
        "ANALYTICS_EXECUTIVE",
        (),
    ),
    "get_sales_analytics": (
        "Sales analytics: summary, trend, products, categories, channels or payment methods.",
        SalesViewArgs,
        get_sales_analytics,
        "ANALYTICS_VIEW",
        ("REPORT_VIEW",),
    ),
    "get_inventory_analytics": (
        "Inventory analytics: stock, turnover, fast, slow or dead movers, reorder, stock-outs.",
        InventoryViewArgs,
        get_inventory_analytics,
        "ANALYTICS_ADVANCED",
        ("INVENTORY_VIEW",),
    ),
    "get_customer_analytics": (
        "Customer analytics: overview, segments or loyalty.",
        CustomerViewArgs,
        get_customer_analytics,
        "ANALYTICS_ADVANCED",
        ("CRM_ANALYTICS_VIEW",),
    ),
    "get_supplier_analytics": (
        "Supplier and purchase analytics: overview, spend by supplier or returns.",
        SupplierViewArgs,
        get_supplier_analytics,
        "ANALYTICS_ADVANCED",
        ("SUPPLIER_VIEW", "PURCHASE_VIEW"),
    ),
    "get_finance_analytics": (
        "Finance analytics: summary, trend, expenses or payment mix.",
        FinanceViewArgs,
        get_finance_analytics,
        "ANALYTICS_ADVANCED",
        ("FINANCE_VIEW",),
    ),
    "get_cohort_report": (
        "Cohort retention by month of first purchase.",
        CohortArgs,
        get_cohort_report,
        "ANALYTICS_ADVANCED",
        ("CRM_ANALYTICS_VIEW",),
    ),
    "get_cross_module_insights": (
        "Facts observed together across modules (never causes).",
        BiArgs,
        get_cross_module_insights,
        "ANALYTICS_ADVANCED",
        (),
    ),
    "get_saved_report": (
        "Run a saved custom report by id or name.",
        SavedReportArgs,
        get_saved_report,
        "ANALYTICS_CUSTOM_REPORT",
        (),
    ),
}
