"""Column types and conversion helpers for money, quantities and timestamps.

Why not floats or NUMERIC?
    Floats cannot represent most decimal amounts exactly (0.1 + 0.2 != 0.3), and SQLite has no real
    NUMERIC type (it silently uses floats). So the database stores exact integers and Python sees
    `Decimal` values:

    Money     integer paise         (Decimal("25.50")  <->  2550)
    Quantity  integer thousandths   (Decimal("2.500")   <->  2500)

    Sums, comparisons and CHECK constraints then work on exact integers on both SQLite and PostgreSQL.

These types never round silently. A value with more decimal places than the column supports raises
`ValueError`, and floats raise `TypeError`. Rounding is a business decision made once, explicitly,
in the service layer (see `round_money` / `round_quantity`).
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.types import TypeDecorator

MONEY_SCALE = 2  # paise per rupee = 10**2
QUANTITY_SCALE = 3  # thousandths per unit = 10**3

_MONEY_STEP = Decimal("0.01")
_QUANTITY_STEP = Decimal("0.001")


def _to_decimal(value: object, what: str) -> Decimal:
    # bool is a subclass of int, so check it explicitly; float is rejected on purpose.
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(
            f"{what} must be a Decimal, int or str, not {type(value).__name__}. "
            "Floating-point values are not allowed."
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{what} {value!r} is not a valid number") from exc
    else:
        raise TypeError(f"{what} must be a Decimal, int or str, not {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{what} must be a finite number")
    return result


def _to_minor_units(value: object, scale: int, what: str) -> int:
    scaled = _to_decimal(value, what).scaleb(scale)
    whole = scaled.to_integral_value()
    if scaled != whole:
        raise ValueError(f"{what} {value} has more than {scale} decimal places")
    return int(whole)


def _from_minor_units(value: object, scale: int, step: Decimal) -> Decimal:
    # `value` is an int from SQLite, or an integral Decimal from PostgreSQL's SUM().
    return Decimal(int(value)).scaleb(-scale).quantize(step)  # type: ignore[call-overload]


def rupees_to_paise(value: object) -> int:
    return _to_minor_units(value, MONEY_SCALE, "Money")


def paise_to_rupees(paise: object) -> Decimal:
    return _from_minor_units(paise, MONEY_SCALE, _MONEY_STEP)


def to_thousandths(value: object) -> int:
    return _to_minor_units(value, QUANTITY_SCALE, "Quantity")


def from_thousandths(thousandths: object) -> Decimal:
    return _from_minor_units(thousandths, QUANTITY_SCALE, _QUANTITY_STEP)


def round_money(value: Decimal) -> Decimal:
    """Round to whole paise, half up. Use for computed amounts (e.g. quantity x price)."""
    return value.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def round_quantity(value: Decimal) -> Decimal:
    """Round to 3 decimal places, half up."""
    return value.quantize(_QUANTITY_STEP, rounding=ROUND_HALF_UP)


class Money(TypeDecorator[Decimal]):
    """Rupee amounts. Stored as integer paise; exposed as `Decimal` with 2 decimal places."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: object, dialect: object) -> int | None:
        return None if value is None else rupees_to_paise(value)

    def process_result_value(self, value: object, dialect: object) -> Decimal | None:
        return None if value is None else paise_to_rupees(value)


class Quantity(TypeDecorator[Decimal]):
    """Stock quantities. Stored as integer thousandths; exposed as `Decimal` with 3 decimal places.

    Whether a product may have a fractional quantity depends on its unit (`units.allows_decimal`).
    That rule needs a lookup in another table, so the service layer enforces it.
    """

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: object, dialect: object) -> int | None:
        return None if value is None else to_thousandths(value)

    def process_result_value(self, value: object, dialect: object) -> Decimal | None:
        return None if value is None else from_thousandths(value)


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timestamps, always UTC.

    Writing a naive datetime raises `ValueError` (it would be ambiguous). Reading always returns a
    timezone-aware UTC datetime, even on SQLite, which drops timezone information.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed; use a timezone-aware datetime (UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
