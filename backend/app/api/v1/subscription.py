"""The shop's plan and entitlements (read-only). Nothing here changes a plan or takes payment."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session
from app.schemas.subscription import SubscriptionOut
from app.services import entitlement_service as plans

router = APIRouter(prefix="/subscription", tags=["subscription"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=SubscriptionOut)
def get_subscription(ctx: Ctx, session: ReadSession) -> SubscriptionOut:
    return SubscriptionOut.build(
        plans.get_entitlements(session, ctx.shop_id),
        plans.usage_summary(session, ctx.shop_id),
        plans.current_period(session, ctx.shop_id),
        plans.list_plans(session),
    )
