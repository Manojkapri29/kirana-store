"""Online store figures for a period, built from the shop's own online orders.

An online order becomes an ordinary Detailed Sale when it is delivered, so its revenue is ALREADY inside every sales, profit and cash figure.
These numbers are therefore never added to revenue: they only describe the online channel (how many orders came in, how many were
delivered, and what the delivered ones came to).
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OnlineOrder
from app.models.enums import OnlineOrderStatus

ZERO = Decimal("0.00")
NOTE = (
    "Online orders are fulfilled as ordinary detailed sales, so their revenue is already inside Detailed sales and is never counted twice. "
    "Counts are orders placed in the period."
)


def period_summary(session: Session, shop_id: int, start: date, end: date) -> dict:
    lo = datetime.combine(start, time.min, tzinfo=UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    by_status = {s.value: 0 for s in OnlineOrderStatus}
    delivered_value = ZERO
    for status, n, value in session.execute(
        select(OnlineOrder.status, func.count(), func.sum(OnlineOrder.sale_total))
        .where(OnlineOrder.shop_id == shop_id, OnlineOrder.placed_at >= lo, OnlineOrder.placed_at < hi)
        .group_by(OnlineOrder.status)
    ):
        by_status[status.value] = n
        if status is OnlineOrderStatus.DELIVERED:
            delivered_value = Decimal(value or 0)
    return {
        "placed": sum(by_status.values()),
        "delivered": by_status["DELIVERED"],
        "rejected_or_cancelled": by_status["REJECTED"] + by_status["CANCELLED"],
        "delivered_value": delivered_value,
        "by_status": by_status,
        "note": NOTE,
    }
