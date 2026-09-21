"""Moving weighted average cost: the arithmetic, with no database and no floats.

The rule (BUSINESS_RULES C1 to C6). Every unit of stock is worth the *average cost* of the stock on hand:

    new average = (old stock x old average + value received) / (old stock + quantity received)

where **value received is the net cost of the goods: quantity x price, minus any discount** (a purchase
line's `line_total`). Averages are kept to whole paise, rounded half up, so results are exact and
repeatable.

Edge cases, decided explicitly:
  * **Nothing on hand** (stock zero, or below zero): the average is simply the incoming cost. The first
    purchase of a product, and a purchase after the shelf emptied, both land here.
  * **Cost unknown** is `None`, never zero. Stock that arrives without a cost (opening stock entered without
    one) leaves the average unknown.
  * **Known cost arriving on top of unknown-cost stock**: the average stays unknown. Pretending the old
    units cost the new price would invent a number. It stays unknown until the shelf empties, and then the
    next purchase sets it. Reports must show "cost incomplete" rather than a made-up figure.
  * **Cost-neutral movements** (adjustments, and later sales) change the stock but never the average.

`replay_average_cost` recomputes the average from a product's history using exactly the same step function
the live update uses, which is what makes the cached `products.avg_cost` rebuildable and testable.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.db.types import round_money
from app.models.enums import InventoryTxnType

ZERO = Decimal("0")


def next_average_cost(
    *,
    stock_before: Decimal,
    avg_before: Decimal | None,
    quantity: Decimal,
    unit_cost: Decimal | None = None,
    line_value: Decimal | None = None,
) -> Decimal | None:
    """The average cost after receiving `quantity` units.

    `line_value` is the exact net value of the receipt (a purchase line's total). If only `unit_cost` is
    known (opening stock), the value is `quantity x unit_cost`. If neither is known, the receipt has no
    cost.
    """
    cost_known = line_value is not None or unit_cost is not None
    if not cost_known:
        return avg_before if stock_before > 0 else None

    if stock_before <= 0:  # nothing on hand: the average is the incoming cost
        if line_value is not None:
            return round_money(line_value / quantity)
        return unit_cost

    if avg_before is None:  # unknown-cost stock is on hand: do not invent a cost for it
        return None

    value = line_value if line_value is not None else quantity * unit_cost  # type: ignore[operator]
    return round_money((stock_before * avg_before + value) / (stock_before + quantity))


def cost_of_goods(quantity: Decimal, unit_cost: Decimal | None) -> Decimal | None:
    """Cost of goods sold for a quantity leaving stock: quantity x cost, whole paise, half up.

    An unknown cost gives `None`, never 0: a sale of goods with no known cost has no known cost of goods,
    and therefore no known profit (BUSINESS_RULES C3, F).
    """
    return None if unit_cost is None else round_money(quantity * unit_cost)


@dataclass(frozen=True)
class CostEvent:
    """One ledger movement, reduced to what costing needs."""

    txn_type: InventoryTxnType
    quantity_delta: Decimal
    unit_cost: Decimal | None = None
    line_value: Decimal | None = None  # exact net value, when the source document knows it


# Movements that bring in stock at a known or unknown cost and therefore move the average.
# A sale return puts goods back at the cost of the line they were sold from (BUSINESS_RULES R2, R5), so it
# moves
# the average like any other receipt. A purchase return only removes stock at the cost it came in at.
_COSTED_RECEIPTS = {InventoryTxnType.OPENING, InventoryTxnType.PURCHASE, InventoryTxnType.SALE_RETURN}


def replay_average_cost(events: Iterable[CostEvent]) -> tuple[Decimal, Decimal | None]:
    """Recompute (stock, average cost) from a history, in the order the movements were recorded.

    Cancelled pairs (a movement and the reversal that undid it) must be left out by the caller: a voided
    purchase then leaves no trace in the average, exactly as if it had never been entered.
    """
    stock, average = ZERO, None
    for event in events:
        if event.txn_type in _COSTED_RECEIPTS and event.quantity_delta > 0:
            average = next_average_cost(
                stock_before=stock,
                avg_before=average,
                quantity=event.quantity_delta,
                unit_cost=event.unit_cost,
                line_value=event.line_value,
            )
        stock += event.quantity_delta  # every movement changes stock; only receipts change the average
    return stock, average
