"""Finance reports and controls that read from the books: cash, payables, receivables, profit and loss,
cash flow, tax, reconciliation, the dashboard and alerts. Every figure comes from a backend service."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import ReconStatus
from app.schemas.finance_reports import (
    CashCountIn,
    CashCountOut,
    CashSummaryOut,
    PayablesOut,
    ReceivablesOut,
    ReconMarkIn,
    ReconMarkOut,
    TaxRateIn,
    TaxRateOut,
    TaxRatePatch,
    TaxSettingsIn,
    TaxSettingsOut,
)
from app.services import (
    cash_service,
    cashflow_service,
    finance_alert_service,
    finance_dashboard_service,
    payables_service,
    pnl_service,
    receivables_service,
    reconciliation_service,
    tax_service,
)
from app.services.errors import InvalidInputError
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/finance", tags=["finance reports"])
ReadSession = Annotated[Session, Depends(get_session)]


def _today(session: Session, shop_id: int) -> date:
    return shop_today(get_shop(session, shop_id))


# --- Cash ----------------------------------------------------------------------------------------------------------


@router.get("/cash/summary", response_model=CashSummaryOut)
def cash_summary(ctx: Ctx, session: ReadSession, day: date | None = None) -> CashSummaryOut:
    return CashSummaryOut.model_validate(
        cash_service.daily_summary(session, ctx.shop_id, day or _today(session, ctx.shop_id))
    )


@router.get("/cash/counts", response_model=list[CashCountOut])
def cash_counts(
    ctx: Ctx, session: ReadSession, date_from: date | None = None, date_to: date | None = None
) -> list[CashCountOut]:
    rows = cash_service.list_counts(session, ctx.shop_id, date_from=date_from, date_to=date_to)
    return [CashCountOut.model_validate(r) for r in rows]


@router.post("/cash/counts", response_model=CashCountOut, status_code=201)
def record_cash_count(payload: CashCountIn, ctx: Ctx) -> CashCountOut:
    with write_transaction() as session:
        count = cash_service.record_count(
            session,
            ctx,
            count_date=payload.count_date,
            actual_cash=payload.actual_cash,
            reason=payload.reason,
            today=_today(session, ctx.shop_id),
        )
        return CashCountOut.model_validate(cash_service._view(count))  # noqa: SLF001


# --- Payables and receivables -------------------------------------------------------------------------------------------


@router.get("/payables", response_model=PayablesOut)
def payables(
    ctx: Ctx, session: ReadSession, as_of: date | None = None, supplier_id: int | None = None
) -> PayablesOut:
    report = payables_service.compute(
        session, ctx.shop_id, as_of or _today(session, ctx.shop_id), supplier_id=supplier_id
    )
    return PayablesOut.model_validate(report)


@router.get("/receivables", response_model=ReceivablesOut)
def receivables(
    ctx: Ctx, session: ReadSession, as_of: date | None = None,
    date_from: Annotated[date | None, Query()] = None, date_to: Annotated[date | None, Query()] = None,
) -> ReceivablesOut:  # fmt: skip
    report = receivables_service.compute(
        session,
        ctx.shop_id,
        as_of or _today(session, ctx.shop_id),
        period_start=date_from,
        period_end=date_to,
    )
    return ReceivablesOut.model_validate(report)


# --- Profit and loss and cash flow --------------------------------------------------------------------------------------


def _period(
    session: Session, shop_id: int, date_from: date | None, date_to: date | None
) -> tuple[date, date]:
    end = date_to or _today(session, shop_id)
    start = date_from or end.replace(day=1)
    if start > end:
        raise InvalidInputError("The start date is after the end date.", field="date_from")
    return start, end


Granularity = Annotated[str, Query(pattern="^(day|week|month)$")]


@router.get("/pnl", response_model=pnl_service.ProfitAndLoss)
def profit_and_loss(
    ctx: Ctx, session: ReadSession, date_from: date | None = None, date_to: date | None = None
) -> pnl_service.ProfitAndLoss:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return pnl_service.compute(session, ctx.shop_id, start, end)


@router.get("/pnl/trend", response_model=list[pnl_service.TrendPoint])
def pnl_trend(
    ctx: Ctx,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: Granularity = "day",
) -> list[pnl_service.TrendPoint]:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return pnl_service.trend(session, ctx.shop_id, start, end, granularity)


@router.get("/cash-flow", response_model=cashflow_service.CashFlowReport)
def cash_flow(
    ctx: Ctx, session: ReadSession, date_from: date | None = None, date_to: date | None = None
) -> cashflow_service.CashFlowReport:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return cashflow_service.compute(session, ctx.shop_id, start, end)


@router.get("/cash-flow/trend", response_model=list[cashflow_service.FlowPoint])
def cash_flow_trend(
    ctx: Ctx,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: Granularity = "day",
) -> list[cashflow_service.FlowPoint]:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return cashflow_service.trend(session, ctx.shop_id, start, end, granularity)


# --- Tax reporting foundation ---------------------------------------------------------------------------------------------


@router.get("/tax/settings", response_model=TaxSettingsOut | None)
def get_tax_settings(ctx: Ctx, session: ReadSession) -> TaxSettingsOut | None:
    row = tax_service.get_settings(session, ctx.shop_id)
    return TaxSettingsOut.model_validate(row) if row else None


@router.put("/tax/settings", response_model=TaxSettingsOut)
def put_tax_settings(payload: TaxSettingsIn, ctx: Ctx) -> TaxSettingsOut:
    with write_transaction() as session:
        return TaxSettingsOut.model_validate(tax_service.configure(session, ctx, **payload.model_dump()))


@router.get("/tax/rates", response_model=list[TaxRateOut])
def list_tax_rates(ctx: Ctx, session: ReadSession) -> list[TaxRateOut]:
    return [TaxRateOut.of(r) for r in tax_service.list_rates(session, ctx.shop_id)]


@router.post("/tax/rates", response_model=TaxRateOut, status_code=201)
def create_tax_rate(payload: TaxRateIn, ctx: Ctx) -> TaxRateOut:
    with write_transaction() as session:
        return TaxRateOut.of(tax_service.create_rate(session, ctx, **payload.model_dump()))


@router.patch("/tax/rates/{rate_id}", response_model=TaxRateOut)
def update_tax_rate(rate_id: int, payload: TaxRatePatch, ctx: Ctx) -> TaxRateOut:
    with write_transaction() as session:
        return TaxRateOut.of(
            tax_service.update_rate(session, ctx, rate_id, **payload.model_dump(exclude_unset=True))
        )


@router.get("/tax/summary", response_model=tax_service.TaxSummary)
def tax_summary(
    ctx: Ctx, session: ReadSession, date_from: date | None = None, date_to: date | None = None
) -> tax_service.TaxSummary:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return tax_service.summary(session, ctx.shop_id, start, end)


# --- Reconciliation ---------------------------------------------------------------------------------------------------------


@router.get("/reconciliation", response_model=reconciliation_service.ReconSummary)
def reconciliation(
    ctx: Ctx,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
    status: ReconStatus | None = None,
    payment_method: str | None = None,
) -> reconciliation_service.ReconSummary:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return reconciliation_service.summary(
        session, ctx.shop_id, start, end, status=status, payment_method=payment_method
    )


@router.post("/reconciliation/marks", response_model=ReconMarkOut, status_code=201)
def mark_reconciliation(payload: ReconMarkIn, ctx: Ctx) -> ReconMarkOut:
    with write_transaction() as session:
        return ReconMarkOut.model_validate(reconciliation_service.mark(session, ctx, **payload.model_dump()))


@router.get("/reconciliation/marks", response_model=list[ReconMarkOut])
def reconciliation_history(
    ctx: Ctx, session: ReadSession, source_type: str, source_id: int
) -> list[ReconMarkOut]:
    return [
        ReconMarkOut.model_validate(m)
        for m in reconciliation_service.history(session, ctx.shop_id, source_type, source_id)
    ]


# --- Dashboard and alerts -----------------------------------------------------------------------------------------------------


@router.get("/dashboard", response_model=finance_dashboard_service.FinanceDashboard)
def dashboard(
    ctx: Ctx,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
    compare: bool = True,
) -> finance_dashboard_service.FinanceDashboard:
    start, end = _period(session, ctx.shop_id, date_from, date_to)
    return finance_dashboard_service.build(
        session, ctx.shop_id, start, end, compare=compare, today=_today(session, ctx.shop_id)
    )


@router.get("/alerts", response_model=list[finance_alert_service.FinanceAlert])
def alerts(ctx: Ctx, session: ReadSession) -> list[finance_alert_service.FinanceAlert]:
    return finance_alert_service.compute(session, ctx.shop_id, _today(session, ctx.shop_id))


@router.post("/alerts/notify", response_model=dict[str, int])
def notify_alerts(ctx: Ctx) -> dict[str, int]:
    """Offers today's alerts to the notification centre (each alert at most once a day)."""
    with write_transaction() as session:
        return {"alerts": finance_alert_service.notify(session, ctx.shop_id, _today(session, ctx.shop_id))}
