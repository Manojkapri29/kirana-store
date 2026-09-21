"""The arithmetic of returns: no database, no floats.

A refund is what the customer really paid for the goods coming back. That is the line's amount after every
discount (the cashier's line and bill discounts and any offer), in proportion to the quantity returned
(BUSINESS_RULES R2). It is worked out cumulatively so rounding never leaves a paisa behind:

    refund now = amount after the cumulative return - amount already refunded

where the amount after a cumulative return of `q` out of `sold` is the whole line net when `q == sold` (the
last return of a line refunds the exact remainder) and `round(net x q / sold)` otherwise.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from app.db.types import round_money
from app.services import promotion_calculation as pc

ZERO = Decimal("0.00")


def cumulative_refund(net: Decimal, sold: Decimal, returned: Decimal) -> Decimal:
    """What has been refunded once `returned` of `sold` units are back."""
    if returned <= 0:
        return ZERO
    if returned >= sold:
        return net
    return round_money(net * returned / sold)


def refund_for(
    net: Decimal, sold: Decimal, returned_before: Decimal, refunded_before: Decimal, quantity: Decimal
) -> Decimal:
    """The refund for returning `quantity` more of a line, given what was returned and refunded already."""
    after = cumulative_refund(net, sold, returned_before + quantity)
    return max(after - refunded_before, ZERO)


def line_nets(
    line_totals: Sequence[Decimal], promotion_discounts: Sequence[Decimal], bill_discount: Decimal
) -> list[Decimal]:
    """What each sale line really came to after the offers and its share of the cashier's bill discount.

    The bill discount is spread over the lines in proportion to what they came to after offers (largest
    remainder), so the nets add up to the sale total exactly."""
    bases = [line - promo for line, promo in zip(line_totals, promotion_discounts, strict=True)]
    weights: Mapping[int, int] = {i: pc.to_paise(base) for i, base in enumerate(bases)}
    shares = pc.spread(pc.to_paise(bill_discount), weights)
    return [base - pc.from_paise(shares.get(i, 0)) for i, base in enumerate(bases)]
