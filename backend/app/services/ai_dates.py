"""Turns words like "today", "last month" or "1 Sep to 15 Sep" into a checked date range. Pure: no database.

The AI never decides a date range or a total: a phrase (typed by a person, or picked by a language model)
comes here,
the backend turns it into real dates in the shop's own calendar, and the numbers are then calculated from the
database.
A phrase this module cannot understand is not guessed at: the caller says so and asks the person to rephrase.

Understands English, Hinglish and Hindi for the common phrases. Weeks start on Monday. Quarters are the
calendar
quarters (Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec; the same as the Indian financial-year quarters). "This financial
year"
runs April to March, like the invoice numbers. A range never reaches into the future: the end is capped at
today.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.errors import InvalidInputError

MAX_RANGE_DAYS = 366 * 3

_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str  # for a person: "today", "1 Sep 2026 to 21 Sep 2026"
    kind: str  # day | week | month | quarter | year | fy | days | custom
    shifted_from: date | None = None  # for kinds that compare "so far": where the current period began

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _text(value: date) -> str:
    return f"{value.day} {calendar.month_abbr[value.month]} {value.year}"


def describe(start: date, end: date) -> str:
    return _text(start) if start == end else f"{_text(start)} to {_text(end)}"


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _fy_start(day: date) -> date:
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def _capped(start: date, end: date, today: date, label: str, kind: str) -> Period:
    return Period(start, min(end, today), label, kind, start)


def _normalise(phrase: str) -> str:
    text = phrase.casefold().strip()
    text = re.sub(r"[?!.,;:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, *needles: str) -> bool:
    return any(re.search(rf"(?<![a-zऀ-ॿ]){re.escape(n)}(?![a-zऀ-ॿ])", text) for n in needles)


_NUMBER_WORDS = {"ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "saat": 7, "das": 10}


_DATE = (
    r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}(?:\s+\d{4})?|[a-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?)"
)
_RANGE = re.compile(_DATE + r"\s+(?:to|till|until|se|-)\s+" + _DATE)


def _parse_date(text: str, today: date) -> date | None:
    """An ISO date (2026-09-01), a day-month (1 sep, 1st september) or a numeric d/m/y."""
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    try:
        if iso:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
        if numeric:
            return date(int(numeric[3]), int(numeric[2]), int(numeric[1]))
        named = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?", text)
        if named and named[2] in _MONTHS:
            year = int(named[3]) if named[3] else today.year
            found = date(year, _MONTHS[named[2]], int(named[1]))
            if not named[3] and found > today:
                found = date(
                    year - 1, found.month, found.day
                )  # "15 dec" said in September means last December
            return found
        month_first = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", text)
        if month_first and month_first[1] in _MONTHS:
            year = int(month_first[3]) if month_first[3] else today.year
            found = date(year, _MONTHS[month_first[1]], int(month_first[2]))
            if not month_first[3] and found > today:
                found = date(year - 1, found.month, found.day)
            return found
    except ValueError:
        return None
    return None


def _month_name(text: str, today: date) -> Period | None:
    """ "september", "in march 2026": the whole of that month (up to today if it is the current one)."""
    for match in re.finditer(r"\b([a-z]{3,9})(?:\s+(\d{4}))?\b", text):
        word = match[1]
        if word not in _MONTHS:
            continue
        if word == "may" and not re.search(r"\bin may\b|\bmay \d{4}\b", text):
            continue  # the ordinary word "may"
        year = int(match[2]) if match[2] else (today.year if _MONTHS[word] <= today.month else today.year - 1)
        start = date(year, _MONTHS[word], 1)
        if start > today:
            return None
        return _capped(
            start, _month_end(start), today, f"{calendar.month_name[start.month]} {start.year}", "month"
        )
    return None


def resolve(phrase: str | None, today: date) -> Period | None:
    """The period a phrase means, or None if it names none. Raises InvalidInputError for an impossible range."""
    if phrase is None or not phrase.strip():
        return None
    text = _normalise(phrase)

    # An explicit range: "2026-09-01 to 2026-09-15", "1 sep to 15 sep", "from 1 sep till 15 sep",
    # anywhere in the text.
    ranged = _RANGE.search(text)
    if ranged:
        first, second = _parse_date(ranged[1].strip(), today), _parse_date(ranged[2].strip(), today)
        if first and second:
            if first > second:
                raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
            if second > today:
                second = today
            if first > today:
                raise InvalidInputError("That date range is in the future.", field="date_from")
            if (second - first).days + 1 > MAX_RANGE_DAYS:
                raise InvalidInputError("That date range is too long (three years at most).", field="date_to")
            return Period(first, second, describe(first, second), "custom")
    single = _parse_date(text, today)
    if single:
        if single > today:
            raise InvalidInputError("That date is in the future.", field="date_from")
        return Period(single, single, _text(single), "day")

    n = re.search(r"(?:last|past|pichle|pichhle|previous|purane)\s+(\d{1,3})\s+(?:days?|din|dino)", text)
    if n:
        count = int(n[1])
        if not 1 <= count <= MAX_RANGE_DAYS:
            raise InvalidInputError("Choose between 1 and 1,095 days.", field="date_from")
        return Period(today - timedelta(days=count - 1), today, f"the last {count} days", "days")

    if _has(text, "yesterday", "beeta hua kal", "गुज़रा कल", "गुजरा कल") or text in ("kal", "कल"):
        day = today - timedelta(days=1)
        return Period(day, day, "yesterday", "day")
    if _has(text, "today", "aaj", "आज", "abhi tak aaj"):
        return Period(today, today, "today", "day")

    last = _has(text, "last", "previous", "pichle", "pichhle", "pichla", "पिछले", "पिछला", "pichli")
    if _has(text, "week", "hafta", "hafte", "haftey", "हफ्ते", "हफ़्ते", "सप्ताह"):
        monday = today - timedelta(days=today.weekday())
        if last:
            start = monday - timedelta(days=7)
            return Period(start, start + timedelta(days=6), "last week", "week")
        return _capped(monday, monday + timedelta(days=6), today, "this week", "week")
    if _has(text, "quarter", "timahi", "तिमाही"):
        start = _quarter_start(today)
        if last:
            start = _add_months(start, -3)
            return Period(start, _month_end(_add_months(start, 2)), "last quarter", "quarter")
        return _capped(start, _month_end(_add_months(start, 2)), today, "this quarter", "quarter")
    if _has(text, "financial year", "fiscal year", "fy", "vitt varsh"):
        start = _fy_start(today)
        if last:
            start = date(start.year - 1, 4, 1)
            return Period(start, date(start.year + 1, 3, 31), "last financial year", "fy")
        return _capped(start, date(start.year + 1, 3, 31), today, "this financial year", "fy")
    if _has(text, "year", "saal", "varsh", "साल", "वर्ष"):
        if last:
            return Period(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "last year", "year")
        return _capped(date(today.year, 1, 1), date(today.year, 12, 31), today, "this year", "year")
    if _has(text, "month", "mahine", "mahina", "mahinay", "महीने", "महीना", "मास"):
        start = _month_start(today)
        if last:
            start = _add_months(start, -1)
            return Period(start, _month_end(start), "last month", "month")
        return _capped(start, _month_end(start), today, "this month", "month")
    return _month_name(text, today)


def previous(period: Period, today: date) -> Period:
    """The period to compare with: the one just before, over the same number of elapsed days."""
    if period.kind in ("month", "quarter", "year", "fy"):
        months = {"month": 1, "quarter": 3, "year": 12, "fy": 12}[period.kind]
        start = _add_months(period.start, -months)
        end = min(
            start + timedelta(days=period.days - 1),
            _add_months(period.start, -months + months) - timedelta(days=1),
        )
        label = {
            "month": "the same days of the previous month",
            "quarter": "the same days of the previous quarter",
        }.get(period.kind, "the same days of the previous year")
        return Period(start, end, label, period.kind)
    if period.kind == "week":
        start = period.start - timedelta(days=7)
        return Period(start, start + timedelta(days=period.days - 1), "the previous week", "week")
    length = period.days
    end = period.start - timedelta(days=1)
    start = end - timedelta(days=length - 1)
    return Period(start, end, f"the {length} days before", "days" if period.kind == "days" else period.kind)


def require(phrase: str | None, today: date, *, default: str) -> Period:
    """Resolve a phrase, falling back to `default`; a phrase that names no period is refused rather than guessed."""
    if phrase is None or not phrase.strip():
        found = resolve(default, today)
    else:
        found = resolve(phrase, today)
        if found is None:
            raise InvalidInputError(
                "I could not tell which dates you mean. Try 'today', 'this month', 'last month' or '1 Sep to 15 Sep'.",
                field="period",
            )
    assert found is not None
    return found


def from_dates(date_from: date, date_to: date, today: date) -> Period:
    if date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    if date_from > today:
        raise InvalidInputError("That date range is in the future.", field="date_from")
    end = min(date_to, today)
    if (end - date_from).days + 1 > MAX_RANGE_DAYS:
        raise InvalidInputError("That date range is too long (three years at most).", field="date_to")
    return Period(date_from, end, describe(date_from, end), "custom")
