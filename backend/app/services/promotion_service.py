"""Promotions, discounts and coupons: the one place that decides what a bill gets off.

Generic for every kind of business. Detailed Sales use it today; online orders, campaigns and loyalty offers
can call the same `evaluate` later, so there is exactly one discount system (BUSINESS_RULES PR).

Two halves:
  * managing promotions: create, edit, activate, pause, expire, list, usage. A promotion is never deleted, and
    it never changes a product's MRP, selling price or cost;
  * `evaluate`: given a cart, the customer and an optional coupon code, which promotions apply and what each
    is worth. The frontend only displays this; the backend is authoritative and posting evaluates again.

Which promotions may apply (all must hold): plan includes promotions; status ACTIVE; inside its date window;
an automatic promotion (no coupon code) applies by itself, a coupon promotion only when its code is entered;
the audience matches (everyone, a first-time customer, or listed customers); the total usage limit and the
customer's own limit are not reached. The survivors are handed to `promotion_calculation`, which applies the
stacking rules. Usage counts posted sales only, so voiding a sale gives its use back.

Every query is scoped to the caller's shop, coupon codes included: another shop's code is "not found".
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Category, Customer, Product, Promotion, QuickSale, Sale, SalePromotion, User
from app.models.enums import (
    PromotionAudience,
    PromotionScope,
    PromotionStatus,
    PromotionType,
    SaleStatus,
)
from app.services import entitlement_service
from app.services import promotion_calculation as calc
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop

FEATURE = "promotions"
ZERO = calc.ZERO
MAX_NAME = 120
MAX_DESCRIPTION = 1000
MAX_TARGETS = 500  # products, categories or customers one promotion may name
COUPON_PATTERN_HELP = "3 to 30 letters, digits, '-' or '_'."


# --- What evaluation returns -------------------------------------------------------------------------


@dataclass(frozen=True)
class CartInput:
    """One cart line as billing sees it: `net` is the line after the cashier's own line discount."""

    product_id: int
    quantity: Decimal
    unit_price: Decimal
    net: Decimal


@dataclass(frozen=True)
class AppliedPromotion:
    promotion_id: int
    name: str
    promo_type: PromotionType
    terms: str
    coupon_code: str | None
    amount: Decimal
    basis: str
    position: int


@dataclass(frozen=True)
class NotApplied:
    promotion_id: int | None
    name: str
    reason: str


@dataclass(frozen=True)
class CouponOutcome:
    code: str
    applied: bool
    message: str


@dataclass(frozen=True)
class Evaluation:
    subtotal: Decimal
    applied: list[AppliedPromotion] = field(default_factory=list)
    not_applied: list[NotApplied] = field(default_factory=list)
    per_line: dict[int, Decimal] = field(default_factory=dict)  # cart line index -> its promotion share
    coupon: CouponOutcome | None = None

    @property
    def total(self) -> Decimal:
        return sum((a.amount for a in self.applied), ZERO)


# --- Coupon codes ---------------------------------------------------------------------------------


def normalize_coupon(code: str | None) -> str | None:
    """Codes are compared without case or surrounding spaces. Blank means no coupon."""
    if code is None:
        return None
    cleaned = code.strip().upper()
    return cleaned or None


def _valid_coupon_shape(code: str) -> bool:
    return (
        3 <= len(code) <= 30
        and code[0].isalnum()
        and all(c.isalnum() or c in "-_" for c in code)
        and code.isascii()
    )


# --- Evaluation ----------------------------------------------------------------------------------


def _ids(targets: Any, key: str) -> frozenset[int]:
    values = targets.get(key) if isinstance(targets, dict) else None
    return frozenset(v for v in (values or []) if isinstance(v, int) and not isinstance(v, bool))


def rule_of(promotion: Promotion) -> calc.Rule:
    return calc.Rule(
        id=promotion.id,
        name=promotion.name,
        promo_type=promotion.promo_type,
        scope=promotion.scope,
        priority=promotion.priority,
        stackable=promotion.stackable,
        percent_bp=promotion.percent_bp,
        amount=promotion.amount,
        offer_price=promotion.offer_price,
        buy_quantity=promotion.buy_quantity,
        get_quantity=promotion.get_quantity,
        get_percent_bp=promotion.get_percent_bp,
        max_discount=promotion.max_discount,
        min_cart_value=promotion.min_cart_value,
        min_quantity=promotion.min_quantity,
        product_ids=_ids(promotion.targets, "product_ids"),
        category_ids=_ids(promotion.targets, "category_ids"),
    )


def effective_status(promotion: Promotion, now: datetime) -> PromotionStatus:
    """The status as it really is now: a promotion past its end date is EXPIRED whatever is stored."""
    if promotion.status in (PromotionStatus.ACTIVE, PromotionStatus.PAUSED):
        if promotion.ends_at is not None and promotion.ends_at <= now:
            return PromotionStatus.EXPIRED
    return promotion.status


def is_live(promotion: Promotion, now: datetime) -> bool:
    """Active and inside its date window: the only state in which it can apply."""
    return (
        promotion.status is PromotionStatus.ACTIVE
        and (promotion.starts_at is None or promotion.starts_at <= now)
        and (promotion.ends_at is None or promotion.ends_at > now)
    )


def _usage(
    session: Session, shop_id: int, promotion_ids: Sequence[int], customer_id: int | None
) -> dict[int, tuple[int, int]]:
    """(uses in total, uses by this customer) per promotion, counting posted sales only."""
    if not promotion_ids:
        return {}
    by_customer = (
        func.coalesce(func.sum(case((Sale.customer_id == customer_id, 1), else_=0)), 0)
        if customer_id is not None
        else literal(0)
    )
    rows = session.execute(
        select(SalePromotion.promotion_id, func.count(), by_customer)
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .where(
            SalePromotion.shop_id == shop_id,
            SalePromotion.promotion_id.in_(promotion_ids),
            Sale.status == SaleStatus.POSTED,
        )
        .group_by(SalePromotion.promotion_id)
    ).all()
    return {pid: (int(total), int(mine or 0)) for pid, total, mine in rows}


def is_new_customer(session: Session, shop_id: int, customer_id: int) -> bool:
    """A first-time customer has no posted sale and no posted quick sale in this shop. Decided from the shop's
    own history, never from a flag someone can set."""
    for model in (Sale, QuickSale):
        found = session.scalar(
            select(model.id)
            .where(
                model.shop_id == shop_id, model.customer_id == customer_id, model.status == SaleStatus.POSTED
            )
            .limit(1)
        )
        if found is not None:
            return False
    return True


def _lock_limited(session: Session, shop_id: int) -> None:
    """Take a row lock on every promotion that has a usage limit, in id order, before counting uses. Two
    sales posted at the same moment then cannot both take the last use (SQLite serialises writers anyway)."""
    session.execute(
        select(Promotion.id)
        .where(
            Promotion.shop_id == shop_id,
            or_(Promotion.usage_limit.is_not(None), Promotion.per_customer_limit.is_not(None)),
        )
        .order_by(Promotion.id)
        .with_for_update()
    ).all()


def _ineligible(
    promotion: Promotion,
    now: datetime,
    *,
    customer_id: int | None,
    new_customer: bool,
    usage: tuple[int, int],
) -> str | None:
    """Why this promotion cannot apply to this customer right now, or None if it can."""
    if promotion.status is PromotionStatus.DRAFT:
        return "This offer is not active yet."
    if promotion.status is PromotionStatus.PAUSED:
        return "This offer is paused."
    if promotion.status is PromotionStatus.EXPIRED or (
        promotion.ends_at is not None and promotion.ends_at <= now
    ):
        return "This offer has expired."
    if promotion.starts_at is not None and promotion.starts_at > now:
        return "This offer has not started yet."
    if promotion.audience is PromotionAudience.NEW_CUSTOMER:
        if customer_id is None:
            return "Choose a customer: this offer is for a customer's first order."
        if not new_customer:
            return "This offer is for a customer's first order only."
    if promotion.audience is PromotionAudience.CUSTOMERS:
        if customer_id is None:
            return "Choose a customer: this offer is for selected customers."
        if customer_id not in _ids(promotion.targets, "customer_ids"):
            return "This offer is not available to this customer."
    total_used, used_by_customer = usage
    if promotion.usage_limit is not None and total_used >= promotion.usage_limit:
        return "This offer has been used the most times it allows."
    if promotion.per_customer_limit is not None:
        if customer_id is None:
            return "Choose a customer: this offer has a limit per customer."
        if used_by_customer >= promotion.per_customer_limit:
            return "This customer has already used this offer the most times it allows."
    return None


def evaluate(
    session: Session,
    shop_id: int,
    cart: Sequence[CartInput],
    *,
    customer_id: int | None = None,
    coupon_code: str | None = None,
    manual_discount: Decimal = ZERO,
    at: datetime | None = None,
    lock: bool = False,
) -> Evaluation:
    """Which promotions apply to this cart, and what each gives. Read-only; never raises for a promotion that
    does not fit (that is reported in the result), so a preview or a draft never fails because of an offer.

    `manual_discount` is the cashier's own bill discount: the promotions can never take the bill below zero
    once it is taken off. `lock=True` (posting) locks the promotions that have usage limits before counting.
    """
    code = normalize_coupon(coupon_code)
    subtotal = sum((line.net for line in cart), ZERO)
    empty = Evaluation(subtotal=subtotal)
    entitlements = entitlement_service.get_entitlements(session, shop_id)
    if not entitlements.allows(FEATURE):
        if code is None:
            return empty
        return Evaluation(
            subtotal,
            coupon=CouponOutcome(code, False, f"The {entitlements.plan_name} plan does not include offers."),
        )
    now = at or utc_now()

    coupon_promotion = None
    if code is not None:
        coupon_promotion = session.scalar(
            select(Promotion).where(Promotion.shop_id == shop_id, Promotion.coupon_code == code)
        )
    automatic = list(
        session.scalars(
            select(Promotion)
            .where(
                Promotion.shop_id == shop_id,
                Promotion.status == PromotionStatus.ACTIVE,
                Promotion.coupon_code.is_(None),
            )
            .order_by(Promotion.id)
        )
    )
    candidates = automatic + ([coupon_promotion] if coupon_promotion is not None else [])
    if not cart or not candidates:
        message = None
        if code is not None and coupon_promotion is None:
            message = "Coupon code not found."
        elif code is not None:
            message = "Add items to the bill to use this coupon."
        return Evaluation(
            subtotal, coupon=None if code is None else CouponOutcome(code, False, message or "")
        )

    if lock:
        _lock_limited(session, shop_id)
    usage = _usage(session, shop_id, [p.id for p in candidates], customer_id)
    new_customer = (
        customer_id is not None
        and any(p.audience is PromotionAudience.NEW_CUSTOMER for p in candidates)
        and is_new_customer(session, shop_id, customer_id)
    )

    eligible: list[Promotion] = []
    not_applied: list[NotApplied] = []
    coupon_reason: str | None = None
    for promotion in candidates:
        reason = _ineligible(
            promotion,
            now,
            customer_id=customer_id,
            new_customer=new_customer,
            usage=usage.get(promotion.id, (0, 0)),
        )
        if reason is None:
            eligible.append(promotion)
        elif promotion is coupon_promotion:
            coupon_reason = reason
            not_applied.append(NotApplied(promotion.id, promotion.name, reason))

    category_of = dict(
        session.execute(
            select(Product.id, Product.category_id).where(
                Product.shop_id == shop_id, Product.id.in_({line.product_id for line in cart})
            )
        ).all()
    )
    lines = [
        calc.CartLine(
            index=index,
            product_id=line.product_id,
            category_id=category_of.get(line.product_id, 0),
            quantity=line.quantity,
            unit_price=line.unit_price,
            net=line.net,
        )
        for index, line in enumerate(cart)
    ]
    outcome = calc.apply_promotions(
        [rule_of(p) for p in eligible], lines, budget=max(subtotal - manual_discount, ZERO)
    )

    by_id = {p.id: p for p in eligible}
    applied = [
        AppliedPromotion(
            promotion_id=item.rule.id,
            name=item.rule.name,
            promo_type=item.rule.promo_type,
            terms=item.terms,
            coupon_code=code if by_id[item.rule.id] is coupon_promotion else None,
            amount=item.amount,
            basis=item.basis,
            position=position,
        )
        for position, item in enumerate(outcome.applied, start=1)
    ]
    for skipped in outcome.skipped:
        not_applied.append(NotApplied(skipped.rule.id, skipped.rule.name, skipped.reason))
        if by_id[skipped.rule.id] is coupon_promotion:
            coupon_reason = skipped.reason

    coupon = None
    if code is not None:
        if coupon_promotion is None:
            coupon = CouponOutcome(code, False, "Coupon code not found.")
        elif any(a.coupon_code for a in applied):
            coupon = CouponOutcome(code, True, f"Coupon {code} applied.")
        else:
            coupon = CouponOutcome(code, False, coupon_reason or "This coupon does not apply to this bill.")
    return Evaluation(subtotal, applied, not_applied, outcome.per_line, coupon)


def require_known_coupon(session: Session, shop_id: int, code: str | None) -> str | None:
    """A coupon code being put on a sale must be one of this shop's, and the plan must include offers."""
    normalized = normalize_coupon(code)
    if normalized is None:
        return None
    entitlement_service.require_feature(session, shop_id, FEATURE)
    exists = session.scalar(
        select(Promotion.id).where(Promotion.shop_id == shop_id, Promotion.coupon_code == normalized)
    )
    if exists is None:
        raise InvalidInputError("Coupon code not found.", field="coupon_code")
    return normalized


def record_applied(session: Session, shop_id: int, sale_id: int, applied: Sequence[AppliedPromotion]) -> None:
    """Freeze what the sale got. Replaces any earlier snapshot for the sale (there is none for a draft)."""
    for old in session.scalars(
        select(SalePromotion).where(SalePromotion.shop_id == shop_id, SalePromotion.sale_id == sale_id)
    ):
        session.delete(old)
    session.flush()
    for item in applied:
        session.add(
            SalePromotion(
                shop_id=shop_id,
                sale_id=sale_id,
                promotion_id=item.promotion_id,
                position=item.position,
                name=item.name,
                promo_type=item.promo_type,
                terms=item.terms,
                coupon_code=item.coupon_code,
                discount_amount=item.amount,
                basis=item.basis,
            )
        )
    session.flush()


def applied_for_sale(session: Session, shop_id: int, sale_id: int) -> list[AppliedPromotion]:
    """The snapshot of what a posted (or voided) sale got: reads `sale_promotions`, never `promotions`."""
    rows = session.scalars(
        select(SalePromotion)
        .where(SalePromotion.shop_id == shop_id, SalePromotion.sale_id == sale_id)
        .order_by(SalePromotion.position)
    )
    return [
        AppliedPromotion(
            r.promotion_id,
            r.name,
            r.promo_type,
            r.terms,
            r.coupon_code,
            r.discount_amount,
            r.basis,
            r.position,
        )
        for r in rows
    ]


# --- Managing promotions ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionView:
    promotion: Promotion
    effective_status: PromotionStatus
    is_live: bool
    used_count: int  # posted sales that used it
    discount_given: Decimal  # what those sales got from it
    created_by_name: str
    product_names: list[str]
    category_names: list[str]
    customer_names: list[str]


_EDITABLE = (
    "name", "description", "priority", "stackable", "starts_at", "ends_at", "coupon_code", "audience",
    "percent", "amount", "offer_price", "buy_quantity", "get_quantity", "get_percent", "min_cart_value",
    "min_quantity", "max_discount", "usage_limit", "per_customer_limit", "product_ids", "category_ids",
    "customer_ids",
)  # fmt: skip
_FIXED_AT_CREATION = ("promo_type", "scope")
_BENEFIT_FIELDS = {
    PromotionType.PERCENT: {"percent"},
    PromotionType.AMOUNT: {"amount"},
    PromotionType.OFFER_PRICE: {"offer_price"},
    PromotionType.BUY_X_GET_Y: {"buy_quantity", "get_quantity", "get_percent"},
}


def _get(session: Session, shop_id: int, promotion_id: int, *, lock: bool = False) -> Promotion:
    query = select(Promotion).where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
    promotion = session.scalar(query.with_for_update() if lock else query)
    if promotion is None:
        raise NotFoundError("Promotion not found")  # also the answer for another shop's promotion
    return promotion


def _as_input(promotion: Promotion) -> dict[str, Any]:
    """The editable fields of a stored promotion, as the API takes them (a percentage, not basis points)."""
    return {
        "promo_type": promotion.promo_type,
        "scope": promotion.scope,
        "name": promotion.name,
        "description": promotion.description,
        "priority": promotion.priority,
        "stackable": promotion.stackable,
        "starts_at": promotion.starts_at,
        "ends_at": promotion.ends_at,
        "coupon_code": promotion.coupon_code,
        "audience": promotion.audience,
        "percent": None if promotion.percent_bp is None else Decimal(promotion.percent_bp) / 100,
        "amount": promotion.amount,
        "offer_price": promotion.offer_price,
        "buy_quantity": promotion.buy_quantity,
        "get_quantity": promotion.get_quantity,
        "get_percent": None if promotion.get_percent_bp is None else Decimal(promotion.get_percent_bp) / 100,
        "min_cart_value": promotion.min_cart_value,
        "min_quantity": promotion.min_quantity,
        "max_discount": promotion.max_discount,
        "usage_limit": promotion.usage_limit,
        "per_customer_limit": promotion.per_customer_limit,
        "product_ids": sorted(_ids(promotion.targets, "product_ids")),
        "category_ids": sorted(_ids(promotion.targets, "category_ids")),
        "customer_ids": sorted(_ids(promotion.targets, "customer_ids")),
    }


def _basis_points(percent: Decimal, field_name: str, problems: list[tuple[str | None, str]]) -> int | None:
    try:
        bp = percent * 100
        if bp != bp.to_integral_value() or not (1 <= bp <= 10000):
            raise InvalidOperation
    except InvalidOperation:
        problems.append((field_name, "Enter a percentage above 0 and up to 100, with at most 2 decimals."))
        return None
    return int(bp)


def _to_utc(value: datetime | None, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    return (value.replace(tzinfo=tz) if value.tzinfo is None else value).astimezone(UTC)


def _check_ids(
    session: Session, shop_id: int, model: Any, ids: Sequence[int], field_name: str, label: str
) -> list[tuple[str | None, str]]:
    unique = sorted(set(ids))
    if len(unique) > MAX_TARGETS:
        return [(field_name, f"Choose at most {MAX_TARGETS} {label}.")]
    found = set(session.scalars(select(model.id).where(model.shop_id == shop_id, model.id.in_(unique))))
    missing = [i for i in unique if i not in found]
    return [(field_name, f"Some of the {label} were not found.")] if missing else []


def _clean(session: Session, shop_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete promotion (create, or a stored one with changes merged in) and return the columns
    to store. Every problem is reported together."""
    problems: list[tuple[str | None, str]] = []
    tz = ZoneInfo(get_shop(session, shop_id).timezone)
    promo_type: PromotionType = values["promo_type"]
    scope: PromotionScope = values["scope"]
    out: dict[str, Any] = {"promo_type": promo_type, "scope": scope}

    name = (values.get("name") or "").strip()
    if not name:
        problems.append(("name", "Give the offer a name."))
    elif len(name) > MAX_NAME:
        problems.append(("name", f"The name is too long ({MAX_NAME} characters at most)."))
    out["name"] = name
    description = (values.get("description") or "").strip() or None
    if description and len(description) > MAX_DESCRIPTION:
        problems.append(
            ("description", f"The description is too long ({MAX_DESCRIPTION} characters at most).")
        )
    out["description"] = description
    priority = values.get("priority") or 0
    if not -1000 <= priority <= 1000:
        problems.append(("priority", "Priority must be between -1000 and 1000."))
    out["priority"] = priority
    out["stackable"] = bool(values.get("stackable"))

    # What the offer gives: exactly the fields of its own type.
    allowed = _BENEFIT_FIELDS[promo_type]
    for name_ in {
        "percent",
        "amount",
        "offer_price",
        "buy_quantity",
        "get_quantity",
        "get_percent",
    } - allowed:
        if values.get(name_) is not None:
            problems.append((name_, "This does not apply to this kind of offer."))
    benefit: dict[str, Any] = {
        "percent_bp": None, "amount": None, "offer_price": None,
        "buy_quantity": None, "get_quantity": None, "get_percent_bp": None,
    }  # fmt: skip
    if promo_type is PromotionType.PERCENT:
        if values.get("percent") is None:
            problems.append(("percent", "Enter the percentage off."))
        else:
            benefit["percent_bp"] = _basis_points(values["percent"], "percent", problems)
    elif promo_type is PromotionType.AMOUNT:
        if values.get("amount") is None or values["amount"] <= 0:
            problems.append(("amount", "Enter an amount above zero."))
        else:
            benefit["amount"] = values["amount"]
    elif promo_type is PromotionType.OFFER_PRICE:
        if values.get("offer_price") is None or values["offer_price"] < 0:
            problems.append(("offer_price", "Enter the offer price."))
        else:
            benefit["offer_price"] = values["offer_price"]
    else:
        for key, label in (("buy_quantity", "how many to buy"), ("get_quantity", "how many they get")):
            value = values.get(key)
            if value is None or value < 1 or value > 1000:
                problems.append((key, f"Enter {label} (a whole number, 1 or more)."))
            else:
                benefit[key] = value
        percent = values.get("get_percent")
        benefit["get_percent_bp"] = (
            10000 if percent is None else _basis_points(percent, "get_percent", problems)
        )
    out.update(benefit)
    if promo_type in (PromotionType.OFFER_PRICE, PromotionType.BUY_X_GET_Y) and scope is PromotionScope.CART:
        problems.append(("scope", "This kind of offer needs chosen products or categories."))

    # Who and what it is for.
    product_ids, category_ids, customer_ids = (
        list(values.get(k) or []) for k in ("product_ids", "category_ids", "customer_ids")
    )
    targets: dict[str, list[int]] = {}
    if scope is PromotionScope.PRODUCTS:
        if not product_ids:
            problems.append(("product_ids", "Choose at least one product."))
        else:
            problems += _check_ids(session, shop_id, Product, product_ids, "product_ids", "products")
            targets["product_ids"] = sorted(set(product_ids))
        if category_ids:
            problems.append(
                ("category_ids", "Categories are not used when the offer is for chosen products.")
            )
    elif scope is PromotionScope.CATEGORIES:
        if not category_ids:
            problems.append(("category_ids", "Choose at least one category."))
        else:
            problems += _check_ids(session, shop_id, Category, category_ids, "category_ids", "categories")
            targets["category_ids"] = sorted(set(category_ids))
        if product_ids:
            problems.append(("product_ids", "Products are not used when the offer is for categories."))
    elif product_ids or category_ids:
        problems.append(("scope", "An offer on the whole bill has no chosen products or categories."))
    audience: PromotionAudience = values.get("audience") or PromotionAudience.ALL
    out["audience"] = audience
    if audience is PromotionAudience.CUSTOMERS:
        if not customer_ids:
            problems.append(("customer_ids", "Choose at least one customer."))
        else:
            problems += _check_ids(session, shop_id, Customer, customer_ids, "customer_ids", "customers")
            targets["customer_ids"] = sorted(set(customer_ids))
    elif customer_ids:
        problems.append(("customer_ids", "Customers are used only when the offer is for chosen customers."))
    out["targets"] = targets

    coupon = normalize_coupon(values.get("coupon_code"))
    if coupon is not None and not _valid_coupon_shape(coupon):
        problems.append(("coupon_code", f"A coupon code is {COUPON_PATTERN_HELP}"))
    out["coupon_code"] = coupon

    # Conditions.
    for key, minimum, message in (
        ("min_cart_value", 0, "Cannot be negative."),
        ("min_quantity", None, "Must be above zero."),
        ("max_discount", None, "Must be above zero."),
    ):
        value = values.get(key)
        if value is not None and (
            (minimum is not None and value < minimum) or (minimum is None and value <= 0)
        ):
            problems.append((key, message))
        out[key] = value
    for key in ("usage_limit", "per_customer_limit"):
        value = values.get(key)
        if value is not None and value < 1:
            problems.append((key, "Must be 1 or more."))
        out[key] = value
    starts, ends = _to_utc(values.get("starts_at"), tz), _to_utc(values.get("ends_at"), tz)
    if starts is not None and ends is not None and ends <= starts:
        problems.append(("ends_at", "The end must be after the start."))
    out["starts_at"], out["ends_at"] = starts, ends

    if problems:
        raise InvalidInputError(problems[0][1], field=problems[0][0], errors=problems)
    return out


def _snapshot(promotion: Promotion) -> dict[str, Any]:
    return {
        "name": promotion.name,
        "promo_type": promotion.promo_type,
        "scope": promotion.scope,
        "status": promotion.status,
        "priority": promotion.priority,
        "stackable": promotion.stackable,
        "starts_at": promotion.starts_at,
        "ends_at": promotion.ends_at,
        "coupon_code": promotion.coupon_code,
        "audience": promotion.audience,
        "percent_bp": promotion.percent_bp,
        "amount": promotion.amount,
        "offer_price": promotion.offer_price,
        "buy_quantity": promotion.buy_quantity,
        "get_quantity": promotion.get_quantity,
        "get_percent_bp": promotion.get_percent_bp,
        "min_cart_value": promotion.min_cart_value,
        "min_quantity": promotion.min_quantity,
        "max_discount": promotion.max_discount,
        "usage_limit": promotion.usage_limit,
        "per_customer_limit": promotion.per_customer_limit,
        "targets": promotion.targets,
    }


def _flush_unique_coupon(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        if "coupon_code" in str(exc.orig):
            raise ConflictError("Another offer already uses this coupon code.", field="coupon_code") from exc
        raise


def create_promotion(session: Session, ctx: RequestContext, values: dict[str, Any]) -> PromotionView:
    """Create a promotion as a DRAFT. It does nothing until it is activated."""
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    clean = _clean(session, ctx.shop_id, {"audience": PromotionAudience.ALL, **values})
    promotion = Promotion(shop_id=ctx.shop_id, status=PromotionStatus.DRAFT, created_by=ctx.user_id, **clean)
    session.add(promotion)
    _flush_unique_coupon(session)
    record_audit(
        session,
        ctx,
        entity_type="promotion",
        entity_id=promotion.id,
        action="create",
        after=_snapshot(promotion),
    )
    return get_view(session, ctx.shop_id, promotion.id)


def update_promotion(
    session: Session, ctx: RequestContext, promotion_id: int, changes: dict[str, Any]
) -> PromotionView:
    """Change a promotion. Its kind and scope are fixed. Sales that already used it keep their snapshot."""
    promotion = _get(session, ctx.shop_id, promotion_id, lock=True)
    if promotion.status is PromotionStatus.EXPIRED:
        raise ConflictError("This offer has expired and cannot be changed. Create a new one instead.")
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    unknown = set(changes) - set(_EDITABLE)
    if unknown:
        raise InvalidInputError(f"'{sorted(unknown)[0]}' cannot be changed.", field=sorted(unknown)[0])
    before = _snapshot(promotion)
    clean = _clean(session, ctx.shop_id, {**_as_input(promotion), **changes})
    for key, value in clean.items():
        setattr(promotion, key, value)
    _flush_unique_coupon(session)
    record_audit(
        session,
        ctx,
        entity_type="promotion",
        entity_id=promotion.id,
        action="update",
        before=before,
        after=_snapshot(promotion),
    )
    return get_view(session, ctx.shop_id, promotion.id)


def set_status(
    session: Session, ctx: RequestContext, promotion_id: int, target: PromotionStatus
) -> PromotionView:
    """activate (DRAFT or PAUSED -> ACTIVE), pause (ACTIVE -> PAUSED) or expire (any live one -> EXPIRED)."""
    promotion = _get(session, ctx.shop_id, promotion_id, lock=True)
    now = utc_now()
    current = effective_status(promotion, now)
    allowed = {
        PromotionStatus.ACTIVE: (PromotionStatus.DRAFT, PromotionStatus.PAUSED),
        PromotionStatus.PAUSED: (PromotionStatus.ACTIVE,),
        PromotionStatus.EXPIRED: (PromotionStatus.DRAFT, PromotionStatus.ACTIVE, PromotionStatus.PAUSED),
    }
    if target not in allowed:
        raise InvalidInputError("Unsupported status.", field="status")
    if current not in allowed[target]:
        raise ConflictError(
            f"An offer that is {current.value.lower()} cannot be made {target.value.lower()}."
        )
    if target is PromotionStatus.ACTIVE:
        entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
        if promotion.ends_at is not None and promotion.ends_at <= now:
            raise ConflictError("The end date has passed. Change it before activating.", field="ends_at")
    before = _snapshot(promotion)
    promotion.status = target
    session.flush()
    action = {
        PromotionStatus.ACTIVE: "activate",
        PromotionStatus.PAUSED: "pause",
        PromotionStatus.EXPIRED: "expire",
    }[target]
    record_audit(
        session,
        ctx,
        entity_type="promotion",
        entity_id=promotion.id,
        action=action,
        before=before,
        after=_snapshot(promotion),
    )
    return get_view(session, ctx.shop_id, promotion.id)


def _view(session: Session, promotion: Promotion, now: datetime) -> PromotionView:
    shop_id = promotion.shop_id
    used, given = session.execute(
        select(func.count(), func.coalesce(func.sum(SalePromotion.discount_amount), 0))
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .where(
            SalePromotion.shop_id == shop_id,
            SalePromotion.promotion_id == promotion.id,
            Sale.status == SaleStatus.POSTED,
        )
    ).one()
    creator = session.scalar(
        select(User.full_name).where(User.shop_id == shop_id, User.id == promotion.created_by)
    )

    def names(model: Any, key: str) -> list[str]:
        ids = sorted(_ids(promotion.targets, key))
        if not ids:
            return []
        rows = session.execute(
            select(model.id, model.name).where(model.shop_id == shop_id, model.id.in_(ids))
        )
        lookup = dict(rows.all())
        return [lookup[i] for i in ids if i in lookup]

    return PromotionView(
        promotion=promotion,
        effective_status=effective_status(promotion, now),
        is_live=is_live(promotion, now),
        used_count=int(used),
        discount_given=Decimal(given) if isinstance(given, Decimal) else ZERO,
        created_by_name=creator or "",
        product_names=names(Product, "product_ids"),
        category_names=names(Category, "category_ids"),
        customer_names=names(Customer, "customer_ids"),
    )


def get_view(session: Session, shop_id: int, promotion_id: int) -> PromotionView:
    promotion = session.execute(
        select(Promotion)
        .where(Promotion.shop_id == shop_id, Promotion.id == promotion_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if promotion is None:
        raise NotFoundError("Promotion not found")
    return _view(session, promotion, utc_now())


def _status_clause(status: PromotionStatus, now: datetime) -> ColumnElement[bool]:
    ended = and_(Promotion.ends_at.is_not(None), Promotion.ends_at <= now)
    running = Promotion.status.in_([PromotionStatus.ACTIVE, PromotionStatus.PAUSED])
    if status is PromotionStatus.EXPIRED:
        return or_(Promotion.status == PromotionStatus.EXPIRED, and_(running, ended))
    if status in (PromotionStatus.ACTIVE, PromotionStatus.PAUSED):
        return and_(Promotion.status == status, or_(Promotion.ends_at.is_(None), Promotion.ends_at > now))
    return Promotion.status == status


def list_promotions(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    statuses: Sequence[PromotionStatus] | None = None,
    promo_type: PromotionType | None = None,
    coupon_only: bool | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[PromotionView], int]:
    """Promotions, newest first. `statuses` filter on the effective status (an ended one is EXPIRED)."""
    now = utc_now()
    conditions: list[ColumnElement[bool]] = [Promotion.shop_id == shop_id]
    if q and q.strip():
        needle = q.strip().lower()
        conditions.append(
            or_(
                func.lower(Promotion.name).contains(needle, autoescape=True),
                func.lower(func.coalesce(Promotion.coupon_code, "")).contains(needle, autoescape=True),
            )
        )
    if statuses:
        conditions.append(or_(*[_status_clause(s, now) for s in statuses]))
    if promo_type is not None:
        conditions.append(Promotion.promo_type == promo_type)
    if coupon_only is not None:
        conditions.append(
            Promotion.coupon_code.is_not(None) if coupon_only else Promotion.coupon_code.is_(None)
        )
    total = session.scalar(select(func.count()).select_from(Promotion).where(*conditions)) or 0
    query = select(Promotion).where(*conditions).order_by(Promotion.id.desc()).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return [_view(session, p, now) for p in session.scalars(query)], total


# --- Usage (reports and exports) -----------------------------------------------------------------


@dataclass(frozen=True)
class UsageRow:
    use: SalePromotion
    sale: Sale
    customer_name: str | None


def list_usage(
    session: Session,
    shop_id: int,
    *,
    promotion_id: int | None = None,
    coupon_only: bool = False,
    statuses: Sequence[SaleStatus] | None = None,
    date_from: Any = None,
    date_to: Any = None,
) -> list[UsageRow]:
    """Every use of a promotion on a sale, newest sale first. Reads the frozen snapshots."""
    if date_from and date_to and date_from > date_to:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    conditions: list[ColumnElement[bool]] = [SalePromotion.shop_id == shop_id]
    if promotion_id is not None:
        conditions.append(SalePromotion.promotion_id == promotion_id)
    if coupon_only:
        conditions.append(SalePromotion.coupon_code.is_not(None))
    conditions.append(Sale.status.in_(statuses or [SaleStatus.POSTED, SaleStatus.VOID]))
    if date_from is not None:
        conditions.append(Sale.sale_date >= date_from)
    if date_to is not None:
        conditions.append(Sale.sale_date <= date_to)
    rows = session.execute(
        select(SalePromotion, Sale, Customer.name)
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .outerjoin(Customer, (Customer.shop_id == Sale.shop_id) & (Customer.id == Sale.customer_id))
        .where(*conditions)
        .order_by(Sale.sale_date.desc(), Sale.id.desc(), SalePromotion.position)
    ).all()
    return [UsageRow(*row) for row in rows]


# --- Offer prices for display (product cards, scanning) ----------------------------------------------


@dataclass(frozen=True)
class ProductOffer:
    """A promotional price a product has right now. Informational: the base selling price is never changed."""

    promotion_id: int
    name: str
    offer_price: Decimal


def product_offers(session: Session, shop_id: int, products: Sequence[Product]) -> dict[int, ProductOffer]:
    """The lowest live offer price each product has, so a screen can show MRP, selling price and offer price
    side by side. Only offers that need nothing but the product count: automatic (no coupon), for everyone,
    with no minimum bill or quantity. Empty when the plan has no offers."""
    if not products or not entitlement_service.get_entitlements(session, shop_id).allows(FEATURE):
        return {}
    now = utc_now()
    live_offers = session.scalars(
        select(Promotion).where(
            Promotion.shop_id == shop_id,
            Promotion.status == PromotionStatus.ACTIVE,
            Promotion.promo_type == PromotionType.OFFER_PRICE,
            Promotion.coupon_code.is_(None),
            Promotion.audience == PromotionAudience.ALL,
            Promotion.min_cart_value.is_(None),
            Promotion.min_quantity.is_(None),
        )
    )
    best: dict[int, ProductOffer] = {}
    for promotion in live_offers:
        if not is_live(promotion, now) or promotion.offer_price is None:
            continue
        rule = rule_of(promotion)
        for product in products:
            targeted = (
                product.id in rule.product_ids
                if promotion.scope is PromotionScope.PRODUCTS
                else product.category_id in rule.category_ids
            )
            if not targeted or promotion.offer_price >= product.selling_price:
                continue
            current = best.get(product.id)
            if current is None or promotion.offer_price < current.offer_price:
                best[product.id] = ProductOffer(promotion.id, promotion.name, promotion.offer_price)
    return best
