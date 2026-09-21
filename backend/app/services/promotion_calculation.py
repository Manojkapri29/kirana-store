"""The arithmetic of promotions, in one place: no database, no floats.

`promotion_service` decides WHICH promotions may apply (status, dates, coupon, audience, usage limits)
and hands the survivors to `apply_promotions` together with the cart. This module decides what each one
is worth and how they combine, deterministically: the same cart and rules always give the same answer.

Money is worked in whole paise (integers), so nothing is ever rounded twice and shares always add up exactly.

Order of application (this is the whole stacking rule):
  1. Promotions on chosen products or categories come first, then whole-bill promotions (a bill-level
     percentage is worked out on what the bill still comes to after the item-level offers).
  2. Inside each group, higher `priority` first; a tie goes to the older promotion (lower id).
  3. Each promotion is worked out on what the lines still come to after the promotions before it, so a
     line can never be discounted below zero and a promotion never discounts the same rupee twice.
  4. A promotion that is not `stackable` applies only if nothing has applied before it, and once it applies,
     nothing after it does. At most `MAX_PROMOTIONS` promotions apply to one sale.
  5. The total discount can never exceed `budget` (the bill less the cashier's own bill discount), so a bill
     never goes below zero.

A discount that a bill-level or capped promotion earns is spread over the lines it applied to in proportion to
what each line still came to (largest remainder, ties to the earlier line), so each line can report its own
net revenue and the shares add up to the promotion's amount to the paisa.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.db.types import round_money
from app.models.enums import PromotionScope, PromotionType

ZERO = Decimal("0.00")
MAX_PROMOTIONS = 3  # promotions that can apply to one sale
_PAISE = Decimal("0.01")
_BP = Decimal(10000)


@dataclass(frozen=True)
class CartLine:
    index: int  # the line's position in the cart
    product_id: int
    category_id: int
    quantity: Decimal
    unit_price: Decimal
    net: Decimal  # what the line comes to after the cashier's own line discount, before promotions


@dataclass(frozen=True)
class Rule:
    """A promotion as the calculation sees it: only what it needs, none of the storage."""

    id: int
    name: str
    promo_type: PromotionType
    scope: PromotionScope
    priority: int = 0
    stackable: bool = False
    percent_bp: int | None = None
    amount: Decimal | None = None
    offer_price: Decimal | None = None
    buy_quantity: int | None = None
    get_quantity: int | None = None
    get_percent_bp: int | None = None
    max_discount: Decimal | None = None
    min_cart_value: Decimal | None = None
    min_quantity: Decimal | None = None
    product_ids: frozenset[int] = frozenset()
    category_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Applied:
    rule: Rule
    amount: Decimal
    per_line: Mapping[int, Decimal]  # cart line index -> its share, in whole paise summing to `amount`
    terms: str
    basis: str


@dataclass(frozen=True)
class Skipped:
    rule: Rule
    reason: str


@dataclass(frozen=True)
class Outcome:
    applied: list[Applied] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((a.amount for a in self.applied), ZERO)

    @property
    def per_line(self) -> dict[int, Decimal]:
        shares: dict[int, Decimal] = {}
        for applied in self.applied:
            for index, share in applied.per_line.items():
                shares[index] = shares.get(index, ZERO) + share
        return shares


# --- paise helpers ---------------------------------------------------------------------------------


def to_paise(value: Decimal) -> int:
    return int((value / _PAISE).to_integral_value(rounding=ROUND_HALF_UP))


def from_paise(value: int) -> Decimal:
    return (Decimal(value) * _PAISE).quantize(_PAISE)


def percent_of(amount: int, basis_points: int) -> int:
    """`basis_points` (1000 = 10%) of an amount in paise, rounded half up to a whole paisa."""
    return int((Decimal(amount) * basis_points / _BP).to_integral_value(rounding=ROUND_HALF_UP))


def spread(total: int, weights: Mapping[int, int]) -> dict[int, int]:
    """Split `total` paise over the keys in proportion to their weights. The parts always add up to `total`
    exactly: whole paise are given first, then the leftover paise go to the largest fractions (ties: the
    smaller key). A share never exceeds its weight when `total <= sum(weights)`."""
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum <= 0:
        return {}
    shares: dict[int, int] = {}
    fractions: list[tuple[Decimal, int]] = []
    for key in sorted(weights):
        exact = Decimal(total) * weights[key] / weight_sum
        whole = int(exact.to_integral_value(rounding=ROUND_FLOOR))
        shares[key] = whole
        fractions.append((exact - whole, key))
    leftover = total - sum(shares.values())
    for _, key in sorted(fractions, key=lambda f: (-f[0], f[1]))[:leftover]:
        shares[key] += 1
    return {key: share for key, share in shares.items() if share > 0}


# --- describing --------------------------------------------------------------------------------------


def _pct(bp: int) -> str:
    text = f"{Decimal(bp) / 100:f}"
    return (text.rstrip("0").rstrip(".") if "." in text else text) + "%"


def describe_terms(rule: Rule) -> str:
    """The offer in a few words, stored with the sale so history reads the same whatever happens later."""
    if rule.promo_type is PromotionType.PERCENT and rule.percent_bp is not None:
        text = f"{_pct(rule.percent_bp)} off"
    elif rule.promo_type is PromotionType.AMOUNT and rule.amount is not None:
        text = f"{rule.amount:.2f} off"
    elif rule.promo_type is PromotionType.OFFER_PRICE and rule.offer_price is not None:
        text = f"Offer price {rule.offer_price:.2f}"
    elif rule.promo_type is PromotionType.BUY_X_GET_Y and rule.buy_quantity and rule.get_quantity:
        free = rule.get_percent_bp in (None, 10000)
        text = f"Buy {rule.buy_quantity} get {rule.get_quantity} " + (
            "free" if free else f"at {_pct(rule.get_percent_bp or 0)} off"
        )
    else:
        text = "Offer"
    if rule.max_discount is not None:
        text += f" (up to {rule.max_discount:.2f})"
    return text


# --- what one rule is worth ----------------------------------------------------------------------


def _targets(rule: Rule, lines: Sequence[CartLine]) -> list[CartLine]:
    if rule.scope is PromotionScope.CART:
        return list(lines)
    if rule.scope is PromotionScope.PRODUCTS:
        return [line for line in lines if line.product_id in rule.product_ids]
    return [line for line in lines if line.category_id in rule.category_ids]


def _natural_shares(rule: Rule, targets: Sequence[CartLine], left: Mapping[int, int]) -> dict[int, int]:
    """What the rule gives each target line, in paise, before any cap. Never more than the line has left."""
    base = {line.index: left[line.index] for line in targets if left[line.index] > 0}
    if not base:
        return {}
    if rule.promo_type is PromotionType.PERCENT and rule.percent_bp is not None:
        return spread(percent_of(sum(base.values()), rule.percent_bp), base)
    if rule.promo_type is PromotionType.AMOUNT and rule.amount is not None:
        return spread(min(to_paise(rule.amount), sum(base.values())), base)
    if rule.promo_type is PromotionType.OFFER_PRICE and rule.offer_price is not None:
        shares: dict[int, int] = {}
        for line in targets:
            if line.index not in base or line.unit_price <= rule.offer_price:
                continue
            reduction = round_money(line.quantity * (line.unit_price - rule.offer_price))
            share = min(to_paise(reduction), base[line.index])
            if share > 0:
                shares[line.index] = share
        return shares
    if rule.promo_type is PromotionType.BUY_X_GET_Y and rule.buy_quantity and rule.get_quantity:
        return _buy_x_get_y(rule, targets, base)
    return {}


def _buy_x_get_y(rule: Rule, targets: Sequence[CartLine], base: Mapping[int, int]) -> dict[int, int]:
    """Of every (X + Y) whole units of the chosen products, the Y cheapest are discounted (free by default).
    Units of different products pool together; a part-unit (say 1.5 kg) does not count towards a group."""
    assert rule.buy_quantity and rule.get_quantity
    percent = 10000 if rule.get_percent_bp is None else rule.get_percent_bp
    units = {
        line.index: int(line.quantity.to_integral_value(rounding=ROUND_FLOOR))
        for line in targets
        if line.index in base
    }
    group = rule.buy_quantity + rule.get_quantity
    free_units = (sum(units.values()) // group) * rule.get_quantity
    shares: dict[int, int] = {}
    for line in sorted((t for t in targets if t.index in units), key=lambda t: (t.unit_price, t.index)):
        if free_units <= 0:
            break
        taken = min(units[line.index], free_units)
        free_units -= taken
        if taken <= 0:
            continue
        worth = percent_of(to_paise(round_money(line.unit_price * taken)), percent)
        share = min(worth, base[line.index])
        if share > 0:
            shares[line.index] = share
    return shares


def _basis(rule: Rule, targets: Sequence[CartLine], shares: Mapping[int, int]) -> str:
    counted = [t for t in targets if t.index in shares]
    quantity = sum((t.quantity for t in counted), Decimal(0))
    item_word = "item" if len(counted) == 1 else "items"
    eligible = from_paise(sum(to_paise(t.net) for t in counted))
    where = "the whole bill" if rule.scope is PromotionScope.CART else f"{len(counted)} eligible {item_word}"
    return f"{where}, {quantity.normalize():f} units, eligible amount {eligible:.2f}"


# --- putting the rules together ------------------------------------------------------------------


def _order(rule: Rule) -> tuple[int, int, int]:
    return (1 if rule.scope is PromotionScope.CART else 0, -rule.priority, rule.id)


def apply_promotions(
    rules: Sequence[Rule],
    lines: Sequence[CartLine],
    *,
    budget: Decimal | None = None,
    max_promotions: int = MAX_PROMOTIONS,
) -> Outcome:
    """Work out which of the (already eligible) rules apply to the cart, and what each one gives."""
    subtotal = sum((line.net for line in lines), ZERO)
    left = {line.index: to_paise(line.net) for line in lines}
    remaining_budget = to_paise(subtotal if budget is None else min(budget, subtotal))
    applied: list[Applied] = []
    skipped: list[Skipped] = []
    blocked_by: Rule | None = None

    for rule in sorted(rules, key=_order):
        if blocked_by is not None:
            skipped.append(Skipped(rule, f"Cannot be combined with '{blocked_by.name}'."))
            continue
        if len(applied) >= max_promotions:
            skipped.append(Skipped(rule, f"At most {max_promotions} offers apply to one bill."))
            continue
        if applied and not rule.stackable:
            skipped.append(Skipped(rule, "This offer cannot be combined with other offers."))
            continue
        if remaining_budget <= 0:
            skipped.append(Skipped(rule, "Nothing left on the bill to discount."))
            continue
        if rule.min_cart_value is not None and subtotal < rule.min_cart_value:
            skipped.append(
                Skipped(
                    rule, f"Needs a bill of at least {rule.min_cart_value:.2f} (this bill is {subtotal:.2f})."
                )
            )
            continue
        targets = _targets(rule, lines)
        if not targets:
            skipped.append(Skipped(rule, "None of the items in the cart are eligible."))
            continue
        quantity = sum((t.quantity for t in targets), Decimal(0))
        if rule.min_quantity is not None and quantity < rule.min_quantity:
            skipped.append(
                Skipped(
                    rule,
                    f"Needs at least {rule.min_quantity.normalize():f} eligible units "
                    f"(there are {quantity.normalize():f}).",
                )
            )
            continue

        shares = _natural_shares(rule, targets, left)
        total = sum(shares.values())
        if total <= 0:
            skipped.append(Skipped(rule, "It would not lower the price of anything in the cart."))
            continue
        ceiling = (
            remaining_budget
            if rule.max_discount is None
            else min(remaining_budget, to_paise(rule.max_discount))
        )
        if total > ceiling:
            shares = spread(
                ceiling, shares
            )  # keep each line's share in proportion to what it was going to get
            total = sum(shares.values())
        for index, share in shares.items():
            left[index] -= share
        remaining_budget -= total
        applied.append(
            Applied(
                rule=rule,
                amount=from_paise(total),
                per_line={index: from_paise(share) for index, share in sorted(shares.items())},
                terms=describe_terms(rule),
                basis=_basis(rule, targets, shares),
            )
        )
        if not rule.stackable:
            blocked_by = rule
    return Outcome(applied, skipped)
