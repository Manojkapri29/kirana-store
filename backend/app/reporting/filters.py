"""The common vocabulary of every advanced report: a date period (with named presets), a comparison period, the
common filters, and pagination.

Backend-authoritative: the frontend only ever sends a preset name or two dates, and the filter values below; it
never sends SQL or a query fragment. A filter that a particular report cannot honour is REPORTED as ignored
(`FilterEffect.ignored`) instead of being silently dropped, so a number is never presented as filtered when it is not.

Period rules
* Weeks start on Monday. Quarters and years are CALENDAR quarters/years.
* "This week/month/quarter/year" run from the start of the unit up to and including today (so far).
* Comparison, mode ``previous_period`` (the default):
    - for a "this ..." preset: the SAME NUMBER OF ELAPSED DAYS at the start of the previous unit (this month on the
      12th compares with the 1st-12th of last month), never a full month against a part month;
    - for anything else (today, yesterday, last ..., custom): the window of equal length immediately before it.
  Mode ``previous_year``: the same dates one year earlier. Mode ``none``: no comparison.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from app.services.errors import InvalidInputError

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_SPAN_DAYS = 366 * 5


class Preset(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"
    CUSTOM = "custom"


class Channel(StrEnum):
    ALL = "ALL"
    DETAILED = "DETAILED"
    QUICK = "QUICK"
    ONLINE = "ONLINE"


class CompareMode(StrEnum):
    PREVIOUS_PERIOD = "previous_period"
    PREVIOUS_YEAR = "previous_year"
    NONE = "none"


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _end_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def resolve_preset(
    preset: Preset, today: date, custom_from: date | None = None, custom_to: date | None = None
) -> Period:
    if preset is Preset.TODAY:
        return Period(today, today, "Today")
    if preset is Preset.YESTERDAY:
        y = today - timedelta(days=1)
        return Period(y, y, "Yesterday")
    if preset is Preset.THIS_WEEK:
        return Period(today - timedelta(days=today.weekday()), today, "This week")
    if preset is Preset.LAST_WEEK:
        start = today - timedelta(days=today.weekday() + 7)
        return Period(start, start + timedelta(days=6), "Last week")
    if preset is Preset.THIS_MONTH:
        return Period(_month_start(today), today, "This month")
    if preset is Preset.LAST_MONTH:
        start = _add_months(_month_start(today), -1)
        return Period(start, _end_of_month(start), "Last month")
    if preset is Preset.THIS_QUARTER:
        return Period(_quarter_start(today), today, "This quarter")
    if preset is Preset.LAST_QUARTER:
        start = _add_months(_quarter_start(today), -3)
        return Period(start, _end_of_month(_add_months(start, 2)), "Last quarter")
    if preset is Preset.THIS_YEAR:
        return Period(date(today.year, 1, 1), today, "This year")
    if preset is Preset.LAST_YEAR:
        return Period(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "Last year")
    if custom_from is None or custom_to is None:
        raise InvalidInputError("A custom period needs both a start and an end date.", field="date_from")
    return custom_period(custom_from, custom_to)


def custom_period(start: date, end: date) -> Period:
    if start > end:
        raise InvalidInputError("The start date is after the end date.", field="date_from")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise InvalidInputError("Choose a period of at most five years.", field="date_from")
    label = start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"
    return Period(start, end, label)


def comparison_for(preset: Preset, period: Period, mode: CompareMode) -> Period | None:
    """The comparison period, or None. See the module docstring for the exact rule."""
    if mode is CompareMode.NONE:
        return None
    if mode is CompareMode.PREVIOUS_YEAR:
        return Period(
            _add_months(period.start, -12), _add_months(period.end, -12), f"{period.label} (a year earlier)"
        )
    elapsed = period.days
    if preset in (Preset.THIS_WEEK, Preset.THIS_MONTH, Preset.THIS_QUARTER, Preset.THIS_YEAR):
        if preset is Preset.THIS_WEEK:
            prev_start = period.start - timedelta(days=7)
            prev_unit_end = prev_start + timedelta(days=6)
        elif preset is Preset.THIS_MONTH:
            prev_start = _add_months(period.start, -1)
            prev_unit_end = _end_of_month(prev_start)
        elif preset is Preset.THIS_QUARTER:
            prev_start = _add_months(period.start, -3)
            prev_unit_end = _end_of_month(_add_months(prev_start, 2))
        else:
            prev_start = date(period.start.year - 1, 1, 1)
            prev_unit_end = date(period.start.year - 1, 12, 31)
        prev_end = min(prev_start + timedelta(days=elapsed - 1), prev_unit_end)
        return Period(
            prev_start, prev_end, f"Same {elapsed} day(s) of the previous {preset.value.split('_')[1]}"
        )
    prev_end = period.start - timedelta(days=1)
    return Period(prev_end - timedelta(days=elapsed - 1), prev_end, "Previous period")


@dataclass(frozen=True)
class FilterEffect:
    applied: list[str]
    ignored: list[str]


@dataclass(frozen=True)
class ReportFilters:
    period: Period
    comparison: Period | None = None
    product_id: int | None = None
    category_id: int | None = None
    brand: str | None = None
    supplier_id: int | None = None
    customer_id: int | None = None
    payment_method: str | None = None
    channel: Channel = Channel.ALL
    active: bool | None = None
    business_type: str | None = None
    preset: Preset = Preset.CUSTOM
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    def supplied(self) -> list[str]:
        names = [
            "product_id",
            "category_id",
            "brand",
            "supplier_id",
            "customer_id",
            "payment_method",
            "active",
            "business_type",
        ]
        out = [n for n in names if getattr(self, n) not in (None, "")]
        if self.channel is not Channel.ALL:
            out.append("channel")
        return out

    def effect(self, honoured: set[str]) -> FilterEffect:
        """Which of the filters the caller sent this report used, and which it cannot apply."""
        given = self.supplied()
        return FilterEffect([n for n in given if n in honoured], [n for n in given if n not in honoured])


def _clean_text_filters(rest: dict) -> dict:
    """Free-text filters are checked before any query: control characters cannot be bound as query values, and a payment method must
    be one that exists. A bad value is a clear refusal, never a database error."""
    from app.models.enums import FinancePaymentMethod, PaymentMethod

    out = dict(rest)
    for name, limit in (("brand", 100), ("business_type", 30)):
        value = out.get(name)
        if value is not None:
            value = str(value).strip()
            if len(value) > limit or any(ord(c) < 32 for c in value):
                raise InvalidInputError(f"'{name}' is not a valid value.", field=name)
            out[name] = value or None
    method = out.get("payment_method")
    if method is not None:
        allowed = {m.value for m in PaymentMethod} | {m.value for m in FinancePaymentMethod}
        method = str(method).strip().upper()
        if method and method not in allowed:
            raise InvalidInputError(
                f"Payment method must be one of: {', '.join(sorted(allowed))}.", field="payment_method"
            )
        out["payment_method"] = method or None
    return out


def build_filters(
    today: date,
    *,
    preset: Preset | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    compare: CompareMode = CompareMode.PREVIOUS_PERIOD,
    compare_from: date | None = None,
    compare_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    **rest,
) -> ReportFilters:  # noqa: ANN003
    if limit < 1 or limit > MAX_LIMIT or offset < 0:
        raise InvalidInputError(
            f"Use a limit between 1 and {MAX_LIMIT} and a non-negative offset.", field="limit"
        )
    rest = _clean_text_filters(rest)
    chosen = preset or (Preset.CUSTOM if date_from or date_to else Preset.THIS_MONTH)
    period = resolve_preset(chosen, today, date_from, date_to)
    if compare_from or compare_to:
        if compare_from is None or compare_to is None:
            raise InvalidInputError("A comparison period needs both dates.", field="compare_from")
        comparison: Period | None = custom_period(compare_from, compare_to)
    else:
        comparison = comparison_for(chosen, period, compare)
    return ReportFilters(
        period=period, comparison=comparison, preset=chosen, limit=limit, offset=offset, **rest
    )


@dataclass
class ReportTable:
    """Rows of one report, one page at a time, with the facts a person needs to trust them."""

    columns: list[tuple[str, str, str]]  # (key, label, kind: text|integer|money|quantity|percent|date)
    rows: list[dict]
    total: int
    limit: int
    offset: int
    title: str = ""
    period: Period | None = None
    comparison: Period | None = None
    notes: list[str] = field(default_factory=list)
    filters_applied: list[str] = field(default_factory=list)
    filters_ignored: list[str] = field(default_factory=list)
    source: str = ""
