"""entitlement_service: "may this shop use this feature?" and "is this shop within its limit?".

The one place these questions are answered. Routes and services call `require_feature`, `check_limit` or
`use_metered`; nothing else looks at plans. The backend is authoritative: the frontend only reads
`get_entitlements` to hide or explain things, and a hidden button is never a protection.

  * A shop's plan is its current subscription (TRIAL or ACTIVE, started, and not past its end date). A shop
    with no current subscription, or whose subscription has run out, gets the **default plan**
    (`DEFAULT_PLAN_CODE`, "free"), so an expired plan never locks anyone out of the basics.
  * A plan entry is a feature switch (`enabled`) or a numeric limit (`limit_value`; NULL means unlimited, 0
    means none allowed). Missing entries are "not allowed" for features and "unlimited" for limits.
  * Metered usage (invoices posted, price lookups made) is counted per shop and calendar month in the shop's
    timezone, in the same transaction as the action being counted.

Plans and their entries are data. `create_plan`, `set_plan_entries` and `assign_plan` are the operator's
tools (see `python -m app.subscription_admin`); there is deliberately no HTTP endpoint that changes a plan
and no payment step: an upgrade is arranged with the operator.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.types import utc_now
from app.models import Plan, PlanFeature, Product, Shop, ShopSubscription, SubscriptionUsage, User
from app.models.enums import BillingInterval, SubscriptionStatus
from app.services.errors import ConflictError, EntitlementError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop

DEFAULT_PLAN_CODE = "free"

# Feature switches and numeric limits the code knows how to check. A plan may carry other keys; they are
# stored but nothing enforces them.
FEATURES = ("barcode_lookup", "promotions", "price_intelligence", "advanced_reports", "online_store")
LIMITS = ("max_products", "max_users", "max_monthly_invoices", "max_price_lookups_per_month")

METRIC_INVOICES = "invoices"
METRIC_PRICE_LOOKUPS = "price_lookups"
_LIMIT_OF_METRIC = {
    METRIC_INVOICES: "max_monthly_invoices",
    METRIC_PRICE_LOOKUPS: "max_price_lookups_per_month",
}

FEATURE_LABELS = {
    "barcode_lookup": "barcode lookup",
    "promotions": "promotions and coupons",
    "price_intelligence": "market price checks",
    "advanced_reports": "advanced reports",
    "online_store": "the online store",
}
LIMIT_LABELS = {
    "max_products": "products",
    "max_users": "users",
    "max_monthly_invoices": "invoices this month",
    "max_price_lookups_per_month": "price checks this month",
}

# What a shop gets if even the default plan row is missing: the basics, nothing extra.
_FALLBACK_LIMITS: dict[str, int | None] = {"max_products": 100, "max_users": 2, "max_monthly_invoices": 100}


@dataclass(frozen=True)
class Entitlements:
    plan_code: str
    plan_name: str
    source: str  # "subscription" or "default"
    status: str | None  # the subscription's status, when there is one
    ends_at: datetime | None
    features: dict[str, bool] = field(default_factory=dict)
    limits: dict[str, int | None] = field(default_factory=dict)  # None = unlimited

    def allows(self, feature: str) -> bool:
        return self.features.get(feature, False)

    def limit(self, key: str) -> int | None:
        return self.limits.get(key)


def _entries(session: Session, plan_id: int) -> tuple[dict[str, bool], dict[str, int | None]]:
    features: dict[str, bool] = {}
    limits: dict[str, int | None] = {}
    for row in session.scalars(select(PlanFeature).where(PlanFeature.plan_id == plan_id)):
        if row.feature_key in LIMITS:
            limits[row.feature_key] = row.limit_value if row.enabled else 0
        else:
            features[row.feature_key] = row.enabled
    return features, limits


def _current_subscription(session: Session, shop_id: int, now: datetime) -> ShopSubscription | None:
    query = (
        select(ShopSubscription)
        .where(
            ShopSubscription.shop_id == shop_id,
            ShopSubscription.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
            ShopSubscription.starts_at <= now,
        )
        .order_by(ShopSubscription.id.desc())
    )
    subscription = session.scalars(query).first()
    if subscription is not None and subscription.ends_at is not None and subscription.ends_at <= now:
        return None  # ran out: the shop falls back to the default plan
    return subscription


def get_entitlements(session: Session, shop_id: int, now: datetime | None = None) -> Entitlements:
    when = now or utc_now()
    subscription = _current_subscription(session, shop_id, when)
    plan = session.get(Plan, subscription.plan_id) if subscription else None
    if plan is not None and not plan.is_active:
        plan = None  # a retired plan gives nothing; the shop is on the default until moved
    if plan is not None and subscription is not None:
        features, limits = _entries(session, plan.id)
        return Entitlements(
            plan.code,
            plan.name,
            "subscription",
            subscription.status.value,
            subscription.ends_at,
            features,
            limits,
        )

    default = session.scalars(select(Plan).where(Plan.code == DEFAULT_PLAN_CODE)).first()
    if default is None:
        return Entitlements(DEFAULT_PLAN_CODE, "Free", "default", None, None, {}, dict(_FALLBACK_LIMITS))
    features, limits = _entries(session, default.id)
    return Entitlements(default.code, default.name, "default", None, None, features, limits)


# --- Checks --------------------------------------------------------------------------------------


def require_feature(session: Session, shop_id: int, feature: str) -> Entitlements:
    """Refuse (403) unless the shop's plan includes `feature`."""
    entitlements = get_entitlements(session, shop_id)
    if not entitlements.allows(feature):
        label = FEATURE_LABELS.get(feature, feature)
        raise EntitlementError(
            f"Your {entitlements.plan_name} plan does not include {label}. Ask to upgrade your plan.",
            feature=feature,
        )
    return entitlements


def check_limit(session: Session, shop_id: int, limit_key: str, current: int, adding: int = 1) -> None:
    """Refuse (403) if `current + adding` would go over the plan's limit."""
    entitlements = get_entitlements(session, shop_id)
    limit = entitlements.limit(limit_key)
    if limit is not None and current + adding > limit:
        label = LIMIT_LABELS.get(limit_key, limit_key)
        raise EntitlementError(
            f"Your {entitlements.plan_name} plan allows {limit} {label}, and you have reached that. "
            "Ask to upgrade your plan.",
            feature=limit_key,
        )


# --- Metered usage -------------------------------------------------------------------------------


def current_period(session: Session, shop_id: int, now: datetime | None = None) -> str:
    """The calendar month in the shop's own timezone, like "2026-09"."""
    shop = get_shop(session, shop_id)
    return (now or utc_now()).astimezone(ZoneInfo(shop.timezone)).strftime("%Y-%m")


def get_usage(session: Session, shop_id: int, metric: str, period: str | None = None) -> int:
    when = period or current_period(session, shop_id)
    count = session.scalar(
        select(SubscriptionUsage.count).where(
            SubscriptionUsage.shop_id == shop_id,
            SubscriptionUsage.period == when,
            SubscriptionUsage.metric == metric,
        )
    )
    return count or 0


def use_metered(session: Session, shop_id: int, metric: str, amount: int = 1) -> int:
    """Count one more use of a metered thing, refusing (403) if the plan's monthly limit is reached.

    Runs inside the caller's transaction, so if the action it counts fails, the count is rolled back with it.
    Voiding does not give a use back: the month's allowance is spent when the thing is done.
    """
    period = current_period(session, shop_id)
    query = select(SubscriptionUsage).where(
        SubscriptionUsage.shop_id == shop_id,
        SubscriptionUsage.period == period,
        SubscriptionUsage.metric == metric,
    )
    row = session.scalar(query.with_for_update())
    used = 0 if row is None else row.count
    limit_key = _LIMIT_OF_METRIC.get(metric)
    if limit_key is not None:
        check_limit(session, shop_id, limit_key, used, amount)
    if row is None:
        row = SubscriptionUsage(shop_id=shop_id, period=period, metric=metric, count=0)
        session.add(row)
    row.count = used + amount
    session.flush()
    return row.count


def check_product_limit(session: Session, shop_id: int) -> None:
    """Refuse to add one more active product beyond the plan's product limit."""
    active = session.scalar(
        select(func.count())
        .select_from(Product)
        .where(Product.shop_id == shop_id, Product.is_active.is_(True))
    )
    check_limit(session, shop_id, "max_products", active or 0)


def usage_summary(session: Session, shop_id: int) -> dict[str, int]:
    """What the shop is using now: active products and users, and this month's metered counters."""
    products = (
        select(func.count())
        .select_from(Product)
        .where(Product.shop_id == shop_id, Product.is_active.is_(True))
    )
    period = current_period(session, shop_id)
    return {
        "products": session.scalar(products) or 0,
        "users": user_count(session, shop_id),
        "invoices": get_usage(session, shop_id, METRIC_INVOICES, period),
        "price_lookups": get_usage(session, shop_id, METRIC_PRICE_LOOKUPS, period),
    }


def user_count(session: Session, shop_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(User).where(User.shop_id == shop_id, User.is_active.is_(True))
        )
        or 0
    )


# --- Reading plans -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanView:
    plan: Plan
    features: dict[str, bool]
    limits: dict[str, int | None]


def list_plans(session: Session, *, include_inactive: bool = False) -> list[PlanView]:
    query = select(Plan).order_by(Plan.sort_order, Plan.id)
    if not include_inactive:
        query = query.where(Plan.is_active.is_(True))
    return [PlanView(plan, *_entries(session, plan.id)) for plan in session.scalars(query)]


def get_plan(session: Session, code: str) -> Plan:
    plan = session.scalars(select(Plan).where(Plan.code == code)).first()
    if plan is None:
        raise NotFoundError(f"Plan '{code}' not found")
    return plan


# --- Operator tools (no HTTP endpoint changes any of this) ---------------------------------------


def create_plan(
    session: Session,
    *,
    code: str,
    name: str,
    description: str | None = None,
    price: Decimal | None = None,
    currency: str = "INR",
    billing_interval: BillingInterval = BillingInterval.MONTHLY,
    sort_order: int = 0,
) -> Plan:
    code = code.strip().lower()
    if not code or not name.strip():
        raise InvalidInputError("A plan needs a code and a name.")
    if price is not None and price < 0:
        raise InvalidInputError("A price cannot be negative.", field="price")
    if session.scalars(select(Plan).where(Plan.code == code)).first() is not None:
        raise ConflictError(f"A plan with the code '{code}' already exists.", field="code")
    plan = Plan(
        code=code, name=name.strip(), description=description, price=price, currency=currency.upper(),
        billing_interval=billing_interval, sort_order=sort_order,
    )  # fmt: skip
    session.add(plan)
    session.flush()
    return plan


def set_plan_entries(
    session: Session,
    code: str,
    *,
    features: dict[str, bool] | None = None,
    limits: dict[str, int | None] | None = None,
) -> PlanView:
    """Set feature switches and limits on a plan (create or replace entries). A limit of None = unlimited."""
    plan = get_plan(session, code)
    existing = {
        row.feature_key: row
        for row in session.scalars(select(PlanFeature).where(PlanFeature.plan_id == plan.id))
    }
    for key, enabled in (features or {}).items():
        row = existing.get(key) or PlanFeature(plan_id=plan.id, feature_key=key)
        row.enabled, row.limit_value = bool(enabled), None
        session.add(row)
    for key, value in (limits or {}).items():
        if value is not None and value < 0:
            raise InvalidInputError(f"The limit for {key} cannot be negative.")
        row = existing.get(key) or PlanFeature(plan_id=plan.id, feature_key=key)
        row.enabled, row.limit_value = True, value
        session.add(row)
    session.flush()
    return PlanView(plan, *_entries(session, plan.id))


def assign_plan(
    session: Session,
    shop_id: int,
    plan_code: str,
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    notes: str | None = None,
) -> ShopSubscription:
    """Put a shop on a plan. The shop's previous current subscription is ended (kept as history)."""
    if status not in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE):
        raise InvalidInputError("A plan is assigned as TRIAL or ACTIVE.", field="status")
    if session.get(Shop, shop_id) is None:
        raise NotFoundError("Shop not found")
    plan = get_plan(session, plan_code)
    if not plan.is_active:
        raise ConflictError(f"The plan '{plan_code}' is not active.")
    begin = starts_at or utc_now()
    if ends_at is not None and ends_at <= begin:
        raise InvalidInputError("The end date must be after the start date.", field="ends_at")
    for old in session.scalars(
        select(ShopSubscription).where(
            ShopSubscription.shop_id == shop_id,
            ShopSubscription.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
        )
    ):
        old.status = SubscriptionStatus.CANCELLED
        old.ends_at = min(old.ends_at, begin) if old.ends_at else begin
    session.flush()
    subscription = ShopSubscription(
        shop_id=shop_id, plan_id=plan.id, status=status, starts_at=begin, ends_at=ends_at, notes=notes
    )
    session.add(subscription)
    session.flush()
    return subscription
