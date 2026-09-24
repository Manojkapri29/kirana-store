"""Cohort and retention analytics.

DEFINITIONS (also in docs/ANALYTICS_COHORTS.md)
* Cohort: identified customers grouped by the CALENDAR MONTH of their first-ever posted purchase (detailed or quick sale that
  names them). "January 2026 cohort" = customers whose first purchase was in January 2026.
* Month N of a cohort: the calendar month N months after the cohort month (Month 0 is the cohort month itself).
* Active customers (month N): cohort customers with at least one purchase in that month.
* Repeat purchasers (month N): active customers that month who made 2+ purchases in that month (month 0) or had already
  purchased in an earlier month (months 1+).
* Retention rate (month N) = active customers in month N / cohort size x 100.
* Revenue = sales to those customers in that month (before returns). Average purchase value = revenue / purchases.

Only months that exist are reported: a cohort is not extended past the current month, the current month is marked partial
(not yet complete), and nothing is extrapolated. A cohort smaller than `SMALL_COHORT` customers carries a warning: its
percentages move a lot with one customer.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy.orm import Session

from app.reporting import facts
from app.reporting.filters import ReportFilters, ReportTable
from app.reporting.sales import _table

ZERO = Decimal("0.00")
SMALL_COHORT = 5
MAX_MONTHS = 24


def _index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _label(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


def cohorts(session: Session, shop_id: int, f: ReportFilters, today: date, months: int = 12) -> ReportTable:
    """Cohorts whose month falls inside the report period (by default the last `months` months are looked at)."""
    if not 1 <= months <= MAX_MONTHS:
        from app.services.errors import InvalidInputError

        raise InvalidInputError(f"Choose between 1 and {MAX_MONTHS} months.", field="months")
    first = facts.first_purchase_dates(session, shop_id)
    current = _index(today)
    lo, hi = _index(f.period.start), min(_index(f.period.end), current)
    members: dict[int, set[int]] = defaultdict(set)
    for cid, day in first.items():
        m = _index(day)
        if lo <= m <= hi and (f.customer_id is None or cid == f.customer_id):
            members[m].add(cid)
    if not members:
        return _table(f, "Cohort retention", _cols(), [], set(), "Identified purchases", _notes())
    earliest = min(members)
    purchases: dict[tuple[int, int], list[Decimal]] = defaultdict(list)  # (customer, month index) -> amounts
    for cid, day, amount, _ in facts.identified_purchases(
        session, shop_id, date(earliest // 12, earliest % 12 + 1, 1), today
    ):
        purchases[(cid, _index(day))].append(amount)
    rows = []
    for m in sorted(members):
        size = len(members[m])
        for offset in range(0, min(current - m, months - 1) + 1):
            idx = m + offset
            active = repeat = 0
            revenue, count = ZERO, 0
            for cid in members[m]:
                got = purchases.get((cid, idx))
                if not got:
                    continue
                active += 1
                revenue += sum(got, ZERO)
                count += len(got)
                earlier = any((cid, k) in purchases for k in range(m, idx))
                if (offset == 0 and len(got) >= 2) or (offset > 0 and earlier):
                    repeat += 1
            rows.append({
                "cohort": _label(m), "cohort_size": size, "month_offset": offset, "month": _label(idx), "active_customers": active,
                "retention_pct": (Decimal(active) / Decimal(size) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
                "repeat_purchasers": repeat, "purchases": count, "revenue": revenue,
                "average_purchase_value": (revenue / count).quantize(Decimal("0.01")) if count else None,
                "partial_month": idx == current,
                "note": ("Small cohort: read with care" if size < SMALL_COHORT else None),
            })  # fmt: skip
    return _table(
        f,
        "Cohort retention",
        _cols(),
        rows,
        {"customer_id"},
        "Identified purchases (posted sales and quick sales that name a customer)",
        _notes(),
    )


def _cols() -> list[tuple[str, str, str]]:
    return [
        ("cohort", "Cohort (first purchase month)", "text"),
        ("cohort_size", "Cohort size", "integer"),
        ("month_offset", "Month", "integer"),
        ("month", "Calendar month", "text"),
        ("active_customers", "Active customers", "integer"),
        ("retention_pct", "Retention %", "percent"),
        ("repeat_purchasers", "Repeat purchasers", "integer"),
        ("purchases", "Purchases", "integer"),
        ("revenue", "Revenue", "money"),
        ("average_purchase_value", "Average purchase value", "money"),
    ]


def _notes() -> list[str]:
    return [
        "Cohort = customers grouped by the month of their first-ever purchase. Month 0 is that month.",
        "Only months that have happened are shown; nothing is extrapolated. The current month is partial.",
        "Walk-in sales that name no customer are not part of any cohort.",
    ]
