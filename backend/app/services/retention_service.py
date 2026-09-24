"""Retention and churn intelligence: factual metrics worked out from real purchase history (`Sale`/`QuickSale`
dates), never a black-box score. Where a rule decides "at risk" or "reactivated," the rule is stated in full —
see `PurchasePattern.at_risk_reason` — matching the brief's own example: a customer is "at risk" because their
gap since the last purchase is unusually long *compared to their own history*, not an unexplained number.

Needs enough history to mean anything; where it doesn't, every figure says "Not enough historical data" rather
than a misleading 0% or 100%.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, QuickSale, Sale
from app.models.enums import SaleStatus

ZERO = Decimal("0")
NOT_ENOUGH_DATA = "Not enough historical data."
DEFAULT_AT_RISK_MULTIPLIER = Decimal("2")


@dataclass(frozen=True)
class PurchasePattern:
    customer_id: int
    purchase_count: int
    average_interval_days: Decimal | None  # None when fewer than 2 purchases: no interval to average
    days_since_last_purchase: int | None
    at_risk: bool
    at_risk_reason: str | None
    reactivated: bool
    reactivated_reason: str | None


@dataclass(frozen=True)
class RetentionSummary:
    period_days: int
    customers_with_purchases: int
    repeat_customers: int  # 2 or more lifetime transactions
    repeat_purchase_rate: (
        Decimal | None
    )  # repeat_customers / customers_with_purchases, or None if 0 customers
    first_time_customers_this_period: int
    returning_customers_this_period: int
    inactive_customer_count: int
    reactivated_count: int
    average_purchase_interval_days: Decimal | None  # shop-wide average, across customers with 2+ purchases
    cohort_retention_rate: Decimal | str  # a Decimal percent, or NOT_ENOUGH_DATA


def _purchase_dates(session: Session, shop_id: int) -> dict[int, list[date]]:
    """Every customer's posted purchase dates (detailed + quick sales), oldest first."""
    out: dict[int, list[date]] = defaultdict(list)
    for customer_id, sale_date in session.execute(
        select(Sale.customer_id, Sale.sale_date).where(
            Sale.shop_id == shop_id, Sale.status == SaleStatus.POSTED, Sale.customer_id.isnot(None)
        )
    ):
        out[customer_id].append(sale_date)
    for customer_id, sale_date in session.execute(
        select(QuickSale.customer_id, QuickSale.sale_date).where(
            QuickSale.shop_id == shop_id,
            QuickSale.status == SaleStatus.POSTED,
            QuickSale.customer_id.isnot(None),
        )
    ):
        out[customer_id].append(sale_date)
    for dates in out.values():
        dates.sort()
    return out


def purchase_patterns(
    session: Session, shop_id: int, today: date, *, at_risk_multiplier: Decimal = DEFAULT_AT_RISK_MULTIPLIER
) -> list[PurchasePattern]:
    """One row per customer who has ever purchased. `at_risk_multiplier` is a parameter, never hardcoded
    inside the rule: "at risk" means the gap since the last purchase is more than `at_risk_multiplier` times
    that customer's own average gap between purchases — stated explicitly in `at_risk_reason`."""
    dates_by_customer = _purchase_dates(session, shop_id)
    out = []
    for customer_id, dates in dates_by_customer.items():
        count = len(dates)
        last = dates[-1]
        days_since_last = (today - last).days
        if count < 2:
            out.append(PurchasePattern(customer_id, count, None, days_since_last, False, None, False, None))
            continue
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, count)]
        average_interval = Decimal(sum(gaps)) / Decimal(len(gaps))
        threshold = average_interval * at_risk_multiplier
        at_risk = Decimal(days_since_last) > threshold
        at_risk_reason = (
            f"No purchase recorded for {days_since_last} days; this customer's typical interval is "
            f"{average_interval.quantize(Decimal('0.1'))} days."
            if at_risk
            else None
        )
        last_gap = Decimal(gaps[-1])
        prior_average = (
            (Decimal(sum(gaps[:-1])) / Decimal(len(gaps[:-1]))) if len(gaps) > 1 else average_interval
        )
        reactivated = len(gaps) > 1 and last_gap > prior_average * at_risk_multiplier
        reactivated_reason = (
            f"Returned after a gap of {gaps[-1]} days, versus a typical interval of "
            f"{prior_average.quantize(Decimal('0.1'))} days before that."
            if reactivated
            else None
        )
        out.append(
            PurchasePattern(
                customer_id, count, average_interval.quantize(Decimal("0.1")), days_since_last,
                at_risk, at_risk_reason, reactivated, reactivated_reason,
            )
        )  # fmt: skip
    return out


def retention_summary(
    session: Session, shop_id: int, today: date, *, period_days: int = 90,
    at_risk_multiplier: Decimal = DEFAULT_AT_RISK_MULTIPLIER,
) -> RetentionSummary:  # fmt: skip
    patterns = purchase_patterns(session, shop_id, today, at_risk_multiplier=at_risk_multiplier)
    dates_by_customer = _purchase_dates(session, shop_id)
    total_customers = len(patterns)
    repeat = sum(1 for p in patterns if p.purchase_count >= 2)
    repeat_rate = (
        (Decimal(repeat) / Decimal(total_customers) * 100).quantize(Decimal("0.1"))
        if total_customers
        else None
    )

    first_time_this_period = 0
    returning_this_period = 0
    for dates in dates_by_customer.values():
        first, last = dates[0], dates[-1]
        if (today - last).days > period_days:
            continue
        if (today - first).days <= period_days:
            first_time_this_period += 1
        else:
            returning_this_period += 1

    inactive_count = sum(
        1
        for p in patterns
        if p.days_since_last_purchase is not None and p.days_since_last_purchase > period_days
    )
    reactivated_count = sum(
        1
        for p in patterns
        if p.reactivated
        and p.days_since_last_purchase is not None
        and p.days_since_last_purchase <= period_days
    )

    multi = [p.average_interval_days for p in patterns if p.average_interval_days is not None]
    shop_average_interval = (
        (sum(multi, ZERO) / Decimal(len(multi))).quantize(Decimal("0.1")) if multi else None
    )

    previous_start = today - timedelta(days=2 * period_days - 1)
    previous_end = today - timedelta(days=period_days)
    previous_cohort = [
        customer_id for customer_id, dates in dates_by_customer.items()
        if previous_start <= dates[0] <= previous_end
    ]  # fmt: skip
    if len(previous_cohort) < 5:
        cohort_rate: Decimal | str = NOT_ENOUGH_DATA
    else:
        current_start = today - timedelta(days=period_days - 1)
        retained = sum(
            1 for cid in previous_cohort if any(d >= current_start for d in dates_by_customer[cid])
        )
        cohort_rate = (Decimal(retained) / Decimal(len(previous_cohort)) * 100).quantize(Decimal("0.1"))

    return RetentionSummary(
        period_days=period_days, customers_with_purchases=total_customers, repeat_customers=repeat,
        repeat_purchase_rate=repeat_rate, first_time_customers_this_period=first_time_this_period,
        returning_customers_this_period=returning_this_period, inactive_customer_count=inactive_count,
        reactivated_count=reactivated_count, average_purchase_interval_days=shop_average_interval,
        cohort_retention_rate=cohort_rate,
    )  # fmt: skip


def reactivation_candidates(
    session: Session, shop_id: int, today: date, *, inactive_days: int = 60
) -> list[int]:
    """Customers who have purchased before but not in the last `inactive_days` — the pool a reactivation
    campaign would target. Excludes deactivated customers."""
    dates_by_customer = _purchase_dates(session, shop_id)
    active_ids = set(
        session.scalars(select(Customer.id).where(Customer.shop_id == shop_id, Customer.is_active.is_(True)))
    )
    return [
        customer_id
        for customer_id, dates in dates_by_customer.items()
        if customer_id in active_ids and (today - dates[-1]).days > inactive_days
    ]
