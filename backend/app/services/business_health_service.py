"""Business health: current period vs. the previous comparable one, for the numbers a shop owner would want to
know changed. This is a thin, additional view over `ai_insights_service.anomalies` (which stays the single
place that decides what counts as unusual) plus a plain side-by-side comparison of the headline metrics — it
never repeats the anomaly logic, and it never assigns a cause: "sales are 18% lower" is a comparison, not an
explanation, and nothing here uses the word "fraud" or any other accusation.

`compare_periods` returns "Not enough historical data" instead of a change whenever the previous period has
too few days of records to mean anything (fewer than `MIN_DAYS_FOR_COMPARISON` days with any sales) — a real
gap in a new shop's history is not disguised as "0% change"."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import ai_format as fmt
from app.services import ai_insights_service, analytics_service, khata_service, sales_report_service

ZERO = Decimal("0")
MIN_DAYS_FOR_COMPARISON = 3
NOT_ENOUGH_DATA = "Not enough historical data."


@dataclass(frozen=True)
class MetricChange:
    label: str
    current: str
    previous: str | None
    change_percent: Decimal | None
    note: str | None = None  # e.g. "Not enough historical data."


@dataclass(frozen=True)
class HealthReport:
    period_label: str
    previous_label: str
    metrics: list[MetricChange]
    anomalies: list[ai_insights_service.Anomaly]
    insights: list[ai_insights_service.Insight]


def _has_enough_history(previous_days_with_sales: int) -> bool:
    return previous_days_with_sales >= MIN_DAYS_FOR_COMPARISON


def compare_periods(
    session: Session, shop_id: int, current: tuple[date, date], previous: tuple[date, date]
) -> list[MetricChange]:
    """Sales, transactions, average bill, purchases, outstanding and discounts: current period against
    `previous`."""
    now = sales_report_service.sales_summary(session, shop_id, *current)
    then = sales_report_service.sales_summary(session, shop_id, *previous)
    enough = _has_enough_history(sum(1 for d in then.days if d.combined.sales_count > 0))

    def change(
        label: str, current_value: Decimal, previous_value: Decimal, formatter=fmt.money
    ) -> MetricChange:
        if not enough:
            return MetricChange(label, formatter(current_value), None, None, NOT_ENOUGH_DATA)
        return MetricChange(
            label,
            formatter(current_value),
            formatter(previous_value),
            fmt.percent_change(current_value, previous_value),
        )

    now_avg = (now.combined.net / now.combined.sales_count) if now.combined.sales_count else ZERO
    then_avg = (then.combined.net / then.combined.sales_count) if then.combined.sales_count else ZERO
    purchases_now = analytics_service.purchase_totals(session, shop_id, *current)
    purchases_then = analytics_service.purchase_totals(session, shop_id, *previous)
    accounts, _ = khata_service.list_accounts(
        session, shop_id, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
    )
    outstanding_now = sum((a.outstanding for a in accounts), ZERO)

    return [
        change("Net sales", now.combined.net, then.combined.net),
        MetricChange(
            "Transactions",
            str(now.combined.sales_count),
            str(then.combined.sales_count) if enough else None,
            fmt.percent_change(Decimal(now.combined.sales_count), Decimal(then.combined.sales_count))
            if enough
            else None,
            None if enough else NOT_ENOUGH_DATA,
        ),  # fmt: skip
        change("Average bill", now_avg, then_avg),
        change("Discounts given", now.combined.discount, then.combined.discount),
        change("Purchases", purchases_now.net, purchases_then.net),
        MetricChange(
            "Customer outstanding (now)",
            fmt.money(outstanding_now),
            None,
            None,
            "A point-in-time balance: no earlier comparison is kept.",
        ),
        change("Sales returns", now.returns_total, then.returns_total),
    ]


def health_report(session: Session, shop_id: int, today: date, *, days: int = 7) -> HealthReport:
    """The current `days`-day period against the same length ending just before it, plus anomalies and
    insights."""
    current = (today - timedelta(days=days - 1), today)
    previous = (current[0] - timedelta(days=days), current[0] - timedelta(days=1))
    return HealthReport(
        period_label=f"{current[0]:%d %b} to {current[1]:%d %b}",
        previous_label=f"{previous[0]:%d %b} to {previous[1]:%d %b}",
        metrics=compare_periods(session, shop_id, current, previous),
        anomalies=ai_insights_service.anomalies(session, shop_id, today),
        insights=ai_insights_service.insights(session, shop_id, today),
    )
