"""The arithmetic of a sale, in one place: no database, no floats.

Every number on a bill comes from these functions: the cart preview, saving a draft, posting, and reports.
The frontend never repeats this arithmetic; it shows what `/sales/calculate` returns.

    line gross   = round(quantity x unit price)                  whole paise, half up
    line total   = line gross - line discount                     the net revenue of the line
    subtotal     = sum of the line totals
    bill total   = subtotal - bill discount                       what the customer pays
    line COGS    = round(quantity x unit cost)                    None when the cost is unknown
    gross profit = bill total - sum of line COGS                  None unless EVERY line's cost is known

Tax is not part of the model (nothing in the schema supports it yet), so there is none here.

Unknown cost is `None`, never 0 (BUSINESS_RULES C3): a profit is only ever reported when it can be computed.
The bill discount reduces the sale's profit but is not spread over the lines, so a line's own profit is
`line total - line COGS`, before any bill discount.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.db.types import round_money
from app.models.enums import PaymentType

ZERO = Decimal("0.00")


class DiscountTooLargeError(ValueError):
    """A discount that is more than the amount it comes off."""


def line_gross(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return round_money(quantity * unit_price)


def line_total(gross: Decimal, discount: Decimal) -> Decimal:
    """The net amount of a line. A discount can bring it to zero but never below."""
    if discount > gross:
        raise DiscountTooLargeError("discount above line amount")
    return gross - discount


@dataclass(frozen=True)
class BillTotals:
    subtotal: Decimal
    discount: Decimal
    total: Decimal


def bill_totals(line_totals: Sequence[Decimal], bill_discount: Decimal = ZERO) -> BillTotals:
    subtotal = sum(line_totals, ZERO)
    if bill_discount > subtotal:
        raise DiscountTooLargeError("discount above bill amount")
    return BillTotals(subtotal, bill_discount, subtotal - bill_discount)


def line_profit(net: Decimal, cogs: Decimal | None) -> Decimal | None:
    """Profit of one line (before any bill discount), or None if its cost is unknown."""
    return None if cogs is None else net - cogs


def gross_profit(total: Decimal, cogs_by_line: Sequence[Decimal | None]) -> Decimal | None:
    """Profit of a whole sale, or None if any line's cost is unknown. Never treats a missing cost as free."""
    if not cogs_by_line or any(cogs is None for cogs in cogs_by_line):
        return None
    return total - sum((c for c in cogs_by_line if c is not None), ZERO)


def total_cogs(cogs_by_line: Sequence[Decimal | None]) -> Decimal | None:
    if not cogs_by_line or any(cogs is None for cogs in cogs_by_line):
        return None
    return sum((c for c in cogs_by_line if c is not None), ZERO)


@dataclass(frozen=True)
class PaymentSplit:
    payment_type: PaymentType
    paid: Decimal
    credit: Decimal  # the part that goes on the customer's khata


def split_payment(total: Decimal, amount_paid: Decimal | None) -> PaymentSplit:
    """`None` means the customer pays in full. Less than the total is credit. More than the total is not a
    sale payment (the caller refuses it); extra money is recorded as an advance on the customer's khata."""
    paid = total if amount_paid is None else amount_paid
    if paid > total:
        raise ValueError("payment above bill total")
    if paid == total:
        return PaymentSplit(PaymentType.PAID, paid, ZERO)
    return PaymentSplit(PaymentType.CREDIT, paid, total - paid)
