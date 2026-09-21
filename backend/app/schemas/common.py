"""Shared field types.

Money and quantities travel as JSON **strings** (`"25.50"`), never as JSON numbers, so no client or
parser can turn them into binary floats on the way. A JSON number with a fraction is refused.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator
from pydantic_core import PydanticCustomError

MONEY_PLACES = 2
QUANTITY_PLACES = 3
MAX_DIGITS = 12  # in total, including the decimals


def _parse_decimal(value: Any, *, places: int, label: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise PydanticCustomError(
            "decimal_as_text", 'Send the {label} as text, for example "25.50".', {"label": label}
        )
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            raise PydanticCustomError("invalid_number", "Enter a valid number.") from None
    else:
        raise PydanticCustomError("invalid_number", "Enter a valid number.")

    if not number.is_finite():
        raise PydanticCustomError("invalid_number", "Enter a valid number.")
    if number < 0:
        raise PydanticCustomError("negative_number", "This cannot be negative.")
    decimals_used = max(0, -number.normalize().as_tuple().exponent)
    if decimals_used > places:
        word = "decimal place" if places == 1 else "decimal places"
        raise PydanticCustomError("too_many_decimals", f"Use at most {places} {word}.")
    if number >= Decimal(10) ** (MAX_DIGITS - places):
        raise PydanticCustomError("number_too_large", "This number is too large.")
    return number


def _money(value: Any) -> Decimal:
    return _parse_decimal(value, places=MONEY_PLACES, label="amount")


def _quantity(value: Any) -> Decimal:
    return _parse_decimal(value, places=QUANTITY_PLACES, label="quantity")


def _percent(value: Any) -> Decimal:
    number = _parse_decimal(value, places=2, label="percentage")
    if not 0 < number <= 100:
        raise PydanticCustomError("percent_range", "Enter a percentage above 0 and up to 100.")
    return number


MoneyIn = Annotated[Decimal, BeforeValidator(_money)]
QuantityIn = Annotated[Decimal, BeforeValidator(_quantity)]
PercentIn = Annotated[Decimal, BeforeValidator(_percent)]  # 12.5 means 12.5%, at most 2 decimals


class Page(BaseModel):
    """Fields shared by paginated lists."""

    total: int
    limit: int
    offset: int
