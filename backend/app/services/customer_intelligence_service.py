"""Customer analytics: read-only figures worked out from the sales and khata records that already exist.
Nothing here writes anything or makes a creditworthiness judgement; balances are shown as plain facts
("Outstanding balance: X"), never as a rating.

Segments are configurable, factual buckets, not labels of character: a "new" or "inactive" customer is
only a statement about *when* they last bought something, and the day thresholds are parameters, not fixed
truths.

`list_analytics` computes every figure with a handful of GROUP BY queries (never one query per customer),
so it stays fast with thousands of customers; `analytics_for` reuses the same bulk queries filtered to one
row, for the same reason a single lookup should not be a different, slower code path."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, OnlineOrder, QuickSale, Sale, SalePromotion
from app.models.enums import OnlineOrderStatus, SaleStatus
from app.services import khata_service
from app.services.errors import NotFoundError

ZERO = Decimal("0")


class CustomerSegment(StrEnum):
    NEW = "NEW"  # first purchase within `new_days`
    ACTIVE = "ACTIVE"  # bought within `active_days`
    INACTIVE = "INACTIVE"  # has bought before, but not within `active_days`
    RECENTLY_INACTIVE = "RECENTLY_INACTIVE"  # no purchase in `active_days`..`long_inactive_days`
    LONG_INACTIVE = "LONG_INACTIVE"  # no purchase in over `long_inactive_days`
    CREDIT = "CREDIT"  # has an outstanding balance right now
    HIGH_FREQUENCY = "HIGH_FREQUENCY"  # at least `frequent_visits` transactions within `active_days`
    LOW_FREQUENCY = (
        "LOW_FREQUENCY"  # has bought, but fewer than `frequent_visits` transactions within `active_days`
    )
    HIGH_VALUE = "HIGH_VALUE"  # lifetime purchases at or above `high_value_threshold` (a shop-set parameter)
    ONE_TIME_BUYER = "ONE_TIME_BUYER"  # exactly one transaction, ever
    FREQUENT_BUYER = "FREQUENT_BUYER"  # at least `frequent_buyer_visits` transactions, ever (lifetime, not
    # just within `active_days` — distinct from HIGH_FREQUENCY)
    COUPON_USER = "COUPON_USER"  # has used at least one coupon-code promotion
    NON_COUPON_USER = "NON_COUPON_USER"  # has purchased, but never used a coupon code


@dataclass(frozen=True)
class CustomerAnalytics:
    customer_id: int
    name: str
    phone: str | None
    is_active: bool
    detailed_sale_count: int
    quick_sale_count: int
    total_purchases: Decimal  # detailed + quick sales, gross billed (not profit)
    average_transaction_value: Decimal | None
    first_purchase: date | None
    last_purchase: date | None
    outstanding: Decimal
    advance: Decimal
    last_payment_date: date | None
    days_since_last_purchase: int | None
    days_since_last_payment: int | None
    segments: list[CustomerSegment]
    online_order_count: int  # orders the shop accepted for this customer (not rejected or cancelled)


def _segments(
    *, first_purchase: date | None, last_purchase: date | None, outstanding: Decimal, transactions: int,
    total_purchases: Decimal, used_coupon: bool, today: date, new_days: int, active_days: int,
    frequent_visits: int, long_inactive_days: int, high_value_threshold: Decimal | None,
    frequent_buyer_visits: int,
) -> list[CustomerSegment]:  # fmt: skip
    segments: list[CustomerSegment] = []
    if first_purchase is not None and (today - first_purchase).days <= new_days:
        segments.append(CustomerSegment.NEW)
    if last_purchase is not None:
        days_since = (today - last_purchase).days
        if days_since <= active_days:
            segments.append(CustomerSegment.ACTIVE)
            segments.append(
                CustomerSegment.HIGH_FREQUENCY
                if transactions >= frequent_visits
                else CustomerSegment.LOW_FREQUENCY
            )
        elif days_since <= long_inactive_days:
            segments.append(CustomerSegment.INACTIVE)
            segments.append(CustomerSegment.RECENTLY_INACTIVE)
        else:
            segments.append(CustomerSegment.INACTIVE)
            segments.append(CustomerSegment.LONG_INACTIVE)
    if outstanding > 0:
        segments.append(CustomerSegment.CREDIT)
    if high_value_threshold is not None and total_purchases >= high_value_threshold:
        segments.append(CustomerSegment.HIGH_VALUE)
    if transactions == 1:
        segments.append(CustomerSegment.ONE_TIME_BUYER)
    elif transactions >= frequent_buyer_visits:
        segments.append(CustomerSegment.FREQUENT_BUYER)
    if transactions > 0:
        segments.append(CustomerSegment.COUPON_USER if used_coupon else CustomerSegment.NON_COUPON_USER)
    return segments


def _online_counts(session: Session, shop_id: int) -> dict[int, int]:
    """Accepted online orders per customer (an order is linked to its customer when accepted)."""
    rows = session.execute(
        select(OnlineOrder.customer_id, func.count())
        .where(
            OnlineOrder.shop_id == shop_id,
            OnlineOrder.customer_id.isnot(None),
            OnlineOrder.status.notin_([OnlineOrderStatus.REJECTED, OnlineOrderStatus.CANCELLED]),
        )
        .group_by(OnlineOrder.customer_id)
    )
    return {customer_id: n for customer_id, n in rows}


def _bulk_rows(
    session: Session, shop_id: int, today: date, *, new_days: int, active_days: int, frequent_visits: int,
    long_inactive_days: int, high_value_threshold: Decimal | None, frequent_buyer_visits: int,
) -> list[CustomerAnalytics]:  # fmt: skip
    customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.shop_id == shop_id))}
    if not customers:
        return []

    detailed = {
        row[0]: row[1:]
        for row in session.execute(
            select(
                Sale.customer_id, func.count(), func.coalesce(func.sum(Sale.total_amount), 0),
                func.min(Sale.sale_date), func.max(Sale.sale_date),
            )
            .where(Sale.shop_id == shop_id, Sale.status == SaleStatus.POSTED, Sale.customer_id.isnot(None))
            .group_by(Sale.customer_id)
        )
    }  # fmt: skip
    quick = {
        row[0]: row[1:]
        for row in session.execute(
            select(
                QuickSale.customer_id, func.count(), func.coalesce(func.sum(QuickSale.gross_amount), 0),
                func.min(QuickSale.sale_date), func.max(QuickSale.sale_date),
            )
            .where(
                QuickSale.shop_id == shop_id, QuickSale.status == SaleStatus.POSTED,
                QuickSale.customer_id.isnot(None),
            )
            .group_by(QuickSale.customer_id)
        )
    }  # fmt: skip
    online = _online_counts(session, shop_id)
    last_payment = khata_service.last_payment_dates(session, shop_id, list(customers))
    accounts = {
        a.customer.id: a for a in khata_service.list_accounts(session, shop_id, active=None, limit=None)[0]
    }
    coupon_users = set(
        session.scalars(
            select(Sale.customer_id.distinct())
            .join(SalePromotion, (SalePromotion.shop_id == Sale.shop_id) & (SalePromotion.sale_id == Sale.id))
            .where(
                Sale.shop_id == shop_id, Sale.customer_id.isnot(None),
                SalePromotion.coupon_code.isnot(None),
            )
        )
    )  # fmt: skip

    out = []
    for customer_id, customer in customers.items():
        d_count, d_total, d_first, d_last = detailed.get(customer_id, (0, Decimal("0"), None, None))
        q_count, q_total, q_first, q_last = quick.get(customer_id, (0, Decimal("0"), None, None))
        first_purchase = min((d for d in (d_first, q_first) if d), default=None)
        last_purchase = max((d for d in (d_last, q_last) if d), default=None)
        transactions = d_count + q_count
        total = Decimal(d_total or 0) + Decimal(q_total or 0)
        account = accounts.get(customer_id)
        outstanding = account.outstanding if account else ZERO
        payment = last_payment.get(customer_id)
        out.append(
            CustomerAnalytics(
                customer_id=customer_id, name=customer.name, phone=customer.phone,
                is_active=customer.is_active,
                detailed_sale_count=d_count, quick_sale_count=q_count, total_purchases=total,
                average_transaction_value=(
                    (total / transactions).quantize(Decimal("0.01")) if transactions else None
                ),
                first_purchase=first_purchase, last_purchase=last_purchase,
                outstanding=outstanding, advance=account.advance if account else ZERO,
                last_payment_date=payment,
                days_since_last_purchase=(today - last_purchase).days if last_purchase else None,
                days_since_last_payment=(today - payment).days if payment else None,
                segments=_segments(
                    first_purchase=first_purchase, last_purchase=last_purchase, outstanding=outstanding,
                    transactions=transactions, total_purchases=total, used_coupon=customer_id in coupon_users,
                    today=today, new_days=new_days, active_days=active_days, frequent_visits=frequent_visits,
                    long_inactive_days=long_inactive_days, high_value_threshold=high_value_threshold,
                    frequent_buyer_visits=frequent_buyer_visits,
                ),
                online_order_count=online.get(customer_id, 0),
            )
        )  # fmt: skip
    return out


def list_analytics(
    session: Session,
    shop_id: int,
    today: date,
    *,
    segment: CustomerSegment | None = None,
    new_days: int = 30,
    active_days: int = 90,
    frequent_visits: int = 4,
    long_inactive_days: int = 180,
    high_value_threshold: Decimal | None = None,
    frequent_buyer_visits: int = 10,
    limit: int | None = 100,
) -> list[CustomerAnalytics]:
    """Every customer's analytics in a handful of queries total, not one per customer."""
    out = _bulk_rows(
        session, shop_id, today, new_days=new_days, active_days=active_days, frequent_visits=frequent_visits,
        long_inactive_days=long_inactive_days, high_value_threshold=high_value_threshold,
        frequent_buyer_visits=frequent_buyer_visits,
    )  # fmt: skip
    if segment is not None:
        out = [a for a in out if segment in a.segments]
    out.sort(key=lambda a: (-(a.outstanding), a.name.casefold()))
    return out[:limit] if limit is not None else out


def analytics_for(
    session: Session,
    shop_id: int,
    customer_id: int,
    today: date,
    *,
    new_days: int = 30,
    active_days: int = 90,
    frequent_visits: int = 4,
    long_inactive_days: int = 180,
    high_value_threshold: Decimal | None = None,
    frequent_buyer_visits: int = 10,
) -> CustomerAnalytics:
    customer = session.get(Customer, customer_id)
    if customer is None or customer.shop_id != shop_id:
        raise NotFoundError("Customer not found")
    for row in _bulk_rows(
        session, shop_id, today, new_days=new_days, active_days=active_days, frequent_visits=frequent_visits,
        long_inactive_days=long_inactive_days, high_value_threshold=high_value_threshold,
        frequent_buyer_visits=frequent_buyer_visits,
    ):  # fmt: skip
        if row.customer_id == customer_id:
            return row
    # A customer with no sales and no ledger history at all: still a valid, empty analytics row.
    return CustomerAnalytics(
        customer_id=customer.id, name=customer.name, phone=customer.phone, is_active=customer.is_active,
        detailed_sale_count=0, quick_sale_count=0, total_purchases=ZERO, average_transaction_value=None,
        first_purchase=None, last_purchase=None, outstanding=ZERO, advance=ZERO, last_payment_date=None,
        days_since_last_purchase=None, days_since_last_payment=None, segments=[],
        online_order_count=_online_counts(session, shop_id).get(customer.id, 0),
    )  # fmt: skip
