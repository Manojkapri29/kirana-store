from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import BillingInterval
from app.services.entitlement_service import FEATURES, LIMITS, Entitlements, PlanView


class PlanOut(BaseModel):
    code: str
    name: str
    description: str | None
    price: Decimal | None  # None = not published: "contact us"
    currency: str
    billing_interval: BillingInterval
    features: dict[str, bool]
    limits: dict[str, int | None]  # None = unlimited
    is_current: bool

    @classmethod
    def from_view(cls, view: PlanView, current_code: str) -> "PlanOut":
        p = view.plan
        return cls(
            code=p.code,
            name=p.name,
            description=p.description,
            price=p.price,
            currency=p.currency,
            billing_interval=p.billing_interval,
            features={key: view.features.get(key, False) for key in FEATURES},
            limits={key: view.limits.get(key) for key in LIMITS},
            is_current=p.code == current_code,
        )


class SubscriptionOut(BaseModel):
    """The shop's plan, what it allows, how much has been used, and the plans it could move to.

    Read-only: no payment, and no endpoint that changes a plan. An upgrade is arranged with the operator.
    """

    plan_code: str
    plan_name: str
    source: str  # "subscription", or "default" when the shop has no current subscription
    status: str | None
    ends_at: datetime | None
    features: dict[str, bool]
    limits: dict[str, int | None]
    usage: dict[str, int]  # products, users, invoices (this month), price_lookups (this month)
    period: str
    plans: list[PlanOut]

    @classmethod
    def build(
        cls, e: Entitlements, usage: dict[str, int], period: str, plans: list[PlanView]
    ) -> "SubscriptionOut":
        return cls(
            plan_code=e.plan_code,
            plan_name=e.plan_name,
            source=e.source,
            status=e.status,
            ends_at=e.ends_at,
            features={key: e.allows(key) for key in FEATURES},
            limits={key: e.limit(key) for key in LIMITS},
            usage=usage,
            period=period,
            plans=[PlanOut.from_view(v, e.plan_code) for v in plans],
        )
