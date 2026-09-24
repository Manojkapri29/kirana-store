"""Advanced reporting and business intelligence. Every figure comes from a backend service in `app/reporting`; the
frontend sends a preset (or two dates) and the common filters, never SQL. Each route needs an analytics permission AND
the data permission of what it shows; a KPI the caller may not see is hidden, not zeroed."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Ctx, feature_flag, meter_export, rate_limited
from app.api.v1.exports import download
from app.db.session import get_session, write_transaction
from app.reporting import (
    builder,
    cohorts,
    crosslinks,
    customers,
    drilldown,
    executive,
    inventory,
    kpis,
    sales,
    suppliers,
)
from app.reporting import finance as finance_reports
from app.reporting.filters import Channel, CompareMode, Preset, ReportFilters, ReportTable, build_filters
from app.schemas.analytics import PreviewIn, SavedReportIn, SavedReportListOut, SavedReportOut
from app.services import analytics_export_service, saved_report_service
from app.services.export_service import ReportFormat
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/analytics", tags=["analytics"])
ReadSession = Annotated[Session, Depends(get_session)]


def report_filters(
    ctx: Ctx,
    session: ReadSession,
    preset: Preset | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    compare: CompareMode = CompareMode.PREVIOUS_PERIOD,
    compare_from: date | None = None,
    compare_to: date | None = None,
    product_id: int | None = None,
    category_id: int | None = None,
    brand: str | None = None,
    supplier_id: int | None = None,
    customer_id: int | None = None,
    payment_method: str | None = None,
    channel: Channel = Channel.ALL,
    active: bool | None = None,
    business_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportFilters:
    today = shop_today(get_shop(session, ctx.shop_id))
    return build_filters(
        today,
        preset=preset,
        date_from=date_from,
        date_to=date_to,
        compare=compare,
        compare_from=compare_from,
        compare_to=compare_to,
        product_id=product_id,
        category_id=category_id,
        brand=brand,
        supplier_id=supplier_id,
        customer_id=customer_id,
        payment_method=payment_method,
        channel=channel,
        active=active,
        business_type=business_type,
        limit=limit,
        offset=offset,
    )


Filters = Annotated[ReportFilters, Depends(report_filters)]


def today_of(session: Session, ctx: Ctx) -> date:
    return shop_today(get_shop(session, ctx.shop_id))


@router.get("/kpis/definitions", response_model=list[kpis.KpiDefinition])
def kpi_definitions(ctx: Ctx) -> list[kpis.KpiDefinition]:
    """What every KPI means: formula, source, unit and limitations. Only KPIs the caller may see are listed."""
    return [d for d, _ in kpis.KPIS.values() if ctx.has(d.permission)]


@router.get("/kpis", response_model=kpis.KpiReport)
def get_kpis(
    ctx: Ctx, session: ReadSession, filters: Filters, key: Annotated[list[str] | None, Query()] = None
) -> kpis.KpiReport:
    return kpis.report(session, ctx.shop_id, filters, today_of(session, ctx), key, set(ctx.granted))


@router.get("/executive", response_model=executive.ExecutiveDashboard)
def executive_dashboard(ctx: Ctx, session: ReadSession, filters: Filters) -> executive.ExecutiveDashboard:
    return executive.build(session, ctx.shop_id, filters, today_of(session, ctx), set(ctx.granted))


# --- Sales analytics ------------------------------------------------------------------------------------------------


@router.get("/sales/summary")
def sales_summary(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return sales.summary(session, ctx.shop_id, filters)


@router.get("/sales/trend", response_model=ReportTable)
def sales_trend(ctx: Ctx, session: ReadSession, filters: Filters, bucket: str = "day") -> ReportTable:
    return sales.trend(session, ctx.shop_id, filters, bucket)


@router.get("/sales/products", response_model=ReportTable)
def sales_products(ctx: Ctx, session: ReadSession, filters: Filters, order: str = "top") -> ReportTable:
    return sales.products(session, ctx.shop_id, filters, order)


@router.get("/sales/categories", response_model=ReportTable)
def sales_categories(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.categories(session, ctx.shop_id, filters)


@router.get("/sales/brands", response_model=ReportTable)
def sales_brands(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.brands(session, ctx.shop_id, filters)


@router.get("/sales/channels", response_model=ReportTable)
def sales_channels(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.channels(session, ctx.shop_id, filters)


@router.get("/sales/payment-methods", response_model=ReportTable)
def sales_payment_methods(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.payment_methods(session, ctx.shop_id, filters)


@router.get("/sales/discounts", response_model=ReportTable)
def sales_discounts(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.discounts(session, ctx.shop_id, filters)


@router.get("/sales/promotions", response_model=ReportTable)
def sales_promotions(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return sales.promotions(session, ctx.shop_id, filters)


# --- Inventory analytics --------------------------------------------------------------------------------------------


@router.get("/inventory/summary")
def inventory_summary(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return inventory.summary(session, ctx.shop_id, filters, today_of(session, ctx))


@router.get("/inventory/stock", response_model=ReportTable)
def inventory_stock(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.stock(session, ctx.shop_id, filters)


@router.get("/inventory/categories", response_model=ReportTable)
def inventory_categories(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.category_summary(session, ctx.shop_id, filters)


@router.get("/inventory/turnover", response_model=ReportTable)
def inventory_turnover(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.turnover(session, ctx.shop_id, filters)


@router.get("/inventory/movers", response_model=ReportTable)
def inventory_movers(ctx: Ctx, session: ReadSession, filters: Filters, kind: str = "fast") -> ReportTable:
    return inventory.movers(session, ctx.shop_id, filters, kind)


@router.get("/inventory/aging", response_model=ReportTable)
def inventory_aging(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.aging(session, ctx.shop_id, filters)


@router.get("/inventory/movement", response_model=ReportTable)
def inventory_movement(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.movement(session, ctx.shop_id, filters)


@router.get("/inventory/purchase-vs-sales", response_model=ReportTable)
def inventory_purchase_vs_sales(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.purchase_vs_sales(session, ctx.shop_id, filters)


@router.get("/inventory/adjustments", response_model=ReportTable)
def inventory_adjustments(
    ctx: Ctx, session: ReadSession, filters: Filters, bucket: str = "month"
) -> ReportTable:
    return inventory.adjustments(session, ctx.shop_id, filters, bucket)


@router.get("/inventory/count-variance", response_model=ReportTable)
def inventory_count_variance(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.count_variance(session, ctx.shop_id, filters)


@router.get("/inventory/reorder", response_model=ReportTable)
def inventory_reorder(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.reorder(session, ctx.shop_id, filters)


@router.get("/inventory/stock-outs", response_model=ReportTable)
def inventory_stock_outs(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return inventory.stock_outs(session, ctx.shop_id, filters)


# --- Customer, CRM and cohort analytics --------------------------------------------------------------------------------


@router.get("/customers/overview")
def customers_overview(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return customers.overview(session, ctx.shop_id, filters, today_of(session, ctx))


@router.get("/customers/list", response_model=ReportTable)
def customers_list(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return customers.customers(session, ctx.shop_id, filters, today_of(session, ctx))


@router.get("/customers/segments", response_model=ReportTable)
def customers_segments(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return customers.segments(session, ctx.shop_id, filters, today_of(session, ctx))


@router.get("/customers/loyalty", response_model=ReportTable)
def customers_loyalty(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return customers.loyalty(session, ctx.shop_id, filters)


@router.get("/customers/campaigns", response_model=ReportTable)
def customers_campaigns(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return customers.campaigns(session, ctx.shop_id, filters)


@router.get("/customers/referrals", response_model=ReportTable)
def customers_referrals(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return customers.referrals(session, ctx.shop_id, filters)


@router.get("/cohorts", response_model=ReportTable)
def cohort_report(
    ctx: Ctx, session: ReadSession, filters: Filters, months: Annotated[int, Query(ge=1, le=24)] = 12
) -> ReportTable:
    return cohorts.cohorts(session, ctx.shop_id, filters, today_of(session, ctx), months)


@router.get("/suppliers/overview")
def suppliers_overview(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return suppliers.overview(session, ctx.shop_id, filters)


@router.get("/suppliers/spend", response_model=ReportTable)
def suppliers_spend(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return suppliers.suppliers(session, ctx.shop_id, filters)


@router.get("/suppliers/products", response_model=ReportTable)
def suppliers_products(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return suppliers.products(session, ctx.shop_id, filters)


@router.get("/suppliers/cost-trend", response_model=ReportTable)
def suppliers_cost_trend(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return suppliers.cost_trend(session, ctx.shop_id, filters)


@router.get("/suppliers/returns", response_model=ReportTable)
def suppliers_returns(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return suppliers.returns(session, ctx.shop_id, filters)


@router.get("/finance/summary")
def finance_summary(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return finance_reports.summary(session, ctx.shop_id, filters)


@router.get("/finance/trend", response_model=ReportTable)
def finance_trend(
    ctx: Ctx,
    session: ReadSession,
    filters: Filters,
    bucket: Annotated[str, Query(pattern="^(day|week|month)$")] = "month",
) -> ReportTable:
    return finance_reports.trend(session, ctx.shop_id, filters, bucket)


@router.get("/finance/expenses", response_model=ReportTable)
def finance_expenses(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return finance_reports.expenses(session, ctx.shop_id, filters)


@router.get("/finance/expense-trend", response_model=ReportTable)
def finance_expense_trend(
    ctx: Ctx,
    session: ReadSession,
    filters: Filters,
    bucket: Annotated[str, Query(pattern="^(day|week|month)$")] = "month",
) -> ReportTable:
    return finance_reports.expense_trend(session, ctx.shop_id, filters, bucket)


@router.get("/finance/payment-mix", response_model=ReportTable)
def finance_payment_mix(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return finance_reports.payment_mix(session, ctx.shop_id, filters)


@router.get("/finance/receivables-aging", response_model=ReportTable)
def finance_receivables_aging(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return finance_reports.aging(session, ctx.shop_id, filters, "receivables")


@router.get("/finance/payables-aging", response_model=ReportTable)
def finance_payables_aging(ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return finance_reports.aging(session, ctx.shop_id, filters, "payables")


@router.get("/finance/tax", response_model=None)
def finance_tax(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    return finance_reports.tax(session, ctx.shop_id, filters)


@router.get("/insights")
def cross_module_insights(ctx: Ctx, session: ReadSession, filters: Filters) -> dict:
    """Facts observed together across modules. Insights whose data permission the caller lacks are listed as hidden."""
    found, hidden = crosslinks.build(session, ctx.shop_id, filters, today_of(session, ctx), set(ctx.granted))
    return {
        "period": filters.period,
        "comparison": filters.comparison,
        "insights": found,
        "hidden": hidden,
        "caution": crosslinks.CAUTION,
    }


# --- Custom report builder --------------------------------------------------------------------------------------
# Every route needs ANALYTICS_CUSTOM_REPORT. What a definition may read is checked again inside the service, against the
# dataset's own permissions, at the moment it is saved or run.


@router.get("/builder/datasets")
def builder_datasets(ctx: Ctx) -> dict:
    return {"datasets": builder.catalog(set(ctx.granted)), "max_rows": builder.MAX_ROWS}


@router.post("/builder/preview", response_model=ReportTable)
def builder_preview(payload: PreviewIn, ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return saved_report_service.preview(
        session, ctx, payload.dataset, payload.definition.model_dump(), filters, today_of(session, ctx)
    )


@router.get("/reports", response_model=SavedReportListOut)
def list_saved_reports(ctx: Ctx, session: ReadSession, include_archived: bool = False) -> SavedReportListOut:
    rows = saved_report_service.list_reports(session, ctx.shop_id, include_archived=include_archived)
    return SavedReportListOut(items=[SavedReportOut.of(r) for r in rows])


@router.post("/reports", response_model=SavedReportOut, status_code=201)
def create_saved_report(payload: SavedReportIn, ctx: Ctx) -> SavedReportOut:
    with write_transaction() as session:
        return SavedReportOut.of(
            saved_report_service.create(
                session,
                ctx,
                name=payload.name,
                description=payload.description,
                dataset=payload.dataset,
                definition=payload.definition.model_dump(),
            )
        )


@router.get("/reports/{report_id}", response_model=SavedReportOut)
def get_saved_report(report_id: int, ctx: Ctx, session: ReadSession) -> SavedReportOut:
    return SavedReportOut.of(saved_report_service.get(session, ctx.shop_id, report_id))


@router.put("/reports/{report_id}", response_model=SavedReportOut)
def update_saved_report(report_id: int, payload: SavedReportIn, ctx: Ctx) -> SavedReportOut:
    with write_transaction() as session:
        return SavedReportOut.of(
            saved_report_service.update(
                session,
                ctx,
                report_id,
                name=payload.name,
                description=payload.description,
                dataset=payload.dataset,
                definition=payload.definition.model_dump(),
            )
        )


@router.post("/reports/{report_id}/archive", response_model=SavedReportOut)
def archive_saved_report(report_id: int, ctx: Ctx) -> SavedReportOut:
    with write_transaction() as session:
        return SavedReportOut.of(saved_report_service.set_archived(session, ctx, report_id, True))


@router.post("/reports/{report_id}/restore", response_model=SavedReportOut)
def restore_saved_report(report_id: int, ctx: Ctx) -> SavedReportOut:
    with write_transaction() as session:
        return SavedReportOut.of(saved_report_service.set_archived(session, ctx, report_id, False))


@router.get("/reports/{report_id}/run", response_model=ReportTable)
def run_saved_report(report_id: int, ctx: Ctx, session: ReadSession, filters: Filters) -> ReportTable:
    return saved_report_service.run(session, ctx, report_id, filters, today_of(session, ctx))


# --- Drill-down --------------------------------------------------------------------------------------------------
# One route per path. `level` and its parameters come from the previous row's `drill` value, so a screen never builds a query.


def _drill_params(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items()}


@router.get("/drill/revenue/{level}", response_model=ReportTable)
def drill_revenue(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.revenue(session, ctx.shop_id, filters, level, _drill_params(request))


@router.get("/drill/inventory/{level}", response_model=ReportTable)
def drill_inventory(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.inventory(session, ctx.shop_id, filters, level, _drill_params(request))


@router.get("/drill/customers/{level}", response_model=ReportTable)
def drill_customers(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.customers(
        session, ctx.shop_id, filters, level, _drill_params(request), today_of(session, ctx)
    )


@router.get("/drill/expenses/{level}", response_model=ReportTable)
def drill_expenses(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.expenses(session, ctx.shop_id, filters, level, _drill_params(request))


@router.get("/drill/suppliers/{level}", response_model=ReportTable)
def drill_suppliers(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.suppliers(session, ctx.shop_id, filters, level, _drill_params(request))


@router.get("/drill/finance/{level}", response_model=ReportTable)
def drill_finance(
    level: str, request: Request, ctx: Ctx, session: ReadSession, filters: Filters
) -> ReportTable:
    return drilldown.finance(session, ctx.shop_id, filters, level, _drill_params(request))


# --- Exports -----------------------------------------------------------------------------------------------------
# The same report functions as the screens (see `catalog`), so a file always matches what is shown.


@router.get("/export")
def exportable_reports(ctx: Ctx) -> dict:
    return {"reports": analytics_export_service.available(ctx), "formats": [f.value for f in ReportFormat]}


@router.get(
    "/export/{report_key}",
    dependencies=[Depends(feature_flag("exports")), Depends(rate_limited("export")), Depends(meter_export)],
)
def export_report(
    report_key: str,
    ctx: Ctx,
    session: ReadSession,
    filters: Filters,
    fmt: Annotated[ReportFormat, Query(alias="format", description="csv, xlsx or pdf")] = ReportFormat.CSV,
    columns: Annotated[
        str | None, Query(max_length=400, description="Comma-separated column keys, in order")
    ] = None,
    group_by: Annotated[str | None, Query(max_length=60)] = None,
    title: Annotated[str | None, Query(max_length=120)] = None,
) -> Response:
    chosen = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    return download(
        analytics_export_service.export_report(
            session, ctx, report_key, fmt, filters, columns=chosen, group_by=group_by, title=title
        )
    )
