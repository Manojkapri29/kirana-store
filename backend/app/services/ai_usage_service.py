"""AI usage: what was asked of the AI layer, for cost control and plan limits.

One row per request that reached it: which feature, which provider and model (if any), whether it worked, and
the
provider's own token counts when it gave them. No question, prompt, answer or document text is ever stored.
The plan's
monthly allowance (`max_ai_requests_per_month`) is counted through the same metered-usage mechanism as
invoices.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import AiUsage
from app.services import entitlement_service
from app.services.ai_provider import Usage
from app.services.shop_service import get_shop

OK = "OK"
FAILED = "FAILED"


def record(
    session: Session,
    ctx: RequestContext,
    *,
    feature: str,
    provider: str | None,
    model: str | None,
    status: str,
    usage: Usage | None = None,
) -> AiUsage:
    row = AiUsage(
        shop_id=ctx.shop_id,
        user_id=ctx.user_id,
        feature=feature,
        provider=provider,
        model=model,
        status=status,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        estimated_cost_micros=usage.cost_micros if usage else None,
        cost_currency=usage.currency if usage else None,
    )
    session.add(row)
    session.flush()
    return row


def check_allowance(session: Session, shop_id: int) -> None:
    """Refuse (403) BEFORE calling a provider if this month's allowance is already spent."""
    entitlement_service.check_limit(
        session,
        shop_id,
        "max_ai_requests_per_month",
        entitlement_service.get_usage(session, shop_id, entitlement_service.METRIC_AI_REQUESTS),
    )


def count_request(session: Session, shop_id: int) -> int:
    """Spend one request of the month's allowance. Runs in the caller's transaction, so a failed answer is not counted."""
    return entitlement_service.use_metered(session, shop_id, entitlement_service.METRIC_AI_REQUESTS)


@dataclass(frozen=True)
class UsageSummary:
    period: str
    requests: int
    limit: int | None
    failed: int
    input_tokens: int
    output_tokens: int
    by_feature: dict[str, int]


def summary(session: Session, shop_id: int) -> UsageSummary:
    period = entitlement_service.current_period(session, shop_id)
    year, month = (int(part) for part in period.split("-"))
    tz = ZoneInfo(get_shop(session, shop_id).timezone)  # the month is the shop's own calendar month
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=tz)
    rows = session.execute(
        select(
            AiUsage.feature,
            AiUsage.status,
            func.count(),
            func.sum(AiUsage.input_tokens),
            func.sum(AiUsage.output_tokens),
        )
        .where(
            AiUsage.shop_id == shop_id,
            AiUsage.created_at >= start,
            AiUsage.created_at < end,
        )
        .group_by(AiUsage.feature, AiUsage.status)
    ).all()
    by_feature: dict[str, int] = {}
    failed = tokens_in = tokens_out = 0
    for feature, status, count, t_in, t_out in rows:
        by_feature[feature] = by_feature.get(feature, 0) + count
        failed += count if status == FAILED else 0
        tokens_in += int(t_in or 0)
        tokens_out += int(t_out or 0)
    entitlements = entitlement_service.get_entitlements(session, shop_id)
    return UsageSummary(
        period,
        entitlement_service.get_usage(session, shop_id, entitlement_service.METRIC_AI_REQUESTS, period),
        entitlements.limit("max_ai_requests_per_month"),
        failed,
        tokens_in,
        tokens_out,
        by_feature,
    )
