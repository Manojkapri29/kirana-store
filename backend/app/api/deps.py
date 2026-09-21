"""Request dependencies shared by all routers."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.core import ratelimit
from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.session import read_session, write_transaction
from app.models.enums import UserRole
from app.services import account_service, context_service, entitlement_service
from app.services.errors import FeatureOffError


def get_request_context() -> RequestContext:
    """The shop and user making the request.

    Development only for now: every request acts as the seeded development owner. Phase 14 replaces
    this function with a lookup from the logged-in user; routers and services do not change. It refuses
    to work in production so a deployment cannot accidentally run without login.
    """
    if get_settings().is_production:
        raise HTTPException(status_code=503, detail="Login is not available yet.")
    with read_session() as session:
        context = context_service.resolve_development_context(session)
    if context is None:
        raise HTTPException(status_code=503, detail="Development user not found. Run: python -m app.seed")
    return context


def _context_for_request(
    request: Request, context: Annotated[RequestContext, Depends(get_request_context)]
) -> RequestContext:
    """The request's context, also remembered on the request so error diagnostics can name the shop and user
    (ids only: never a name or a contact detail)."""
    request.state.shop_id = context.shop_id
    request.state.user_id = context.user_id
    # The shop's account state decides what it may do (suspended and deactivated shops are restricted by
    # policy).
    with read_session() as session:
        blocked = account_service.restriction(session, context.shop_id, request.method, request.url.path)
    if blocked is not None:
        raise blocked
    return context


Ctx = Annotated[RequestContext, Depends(_context_for_request)]


def require_owner(ctx: Ctx) -> RequestContext:
    if ctx.role is not UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Only the shop owner can do this.")
    return ctx


OwnerCtx = Annotated[RequestContext, Depends(require_owner)]


def rate_limited(group: str):  # noqa: ANN201
    """A dependency that counts this call against the shop's allowance for `group`.

    Beyond the allowance the answer is HTTP 429 with Retry-After.
    """

    def dependency(ctx: Ctx) -> None:
        ratelimit.enforce(group, f"shop{ctx.shop_id}:user{ctx.user_id}")

    return dependency


def feature_flag(name: str):  # noqa: ANN201
    """A dependency that refuses (HTTP 503) when the operator has switched the capability off for everyone."""

    def dependency() -> None:
        if not getattr(get_settings(), f"feature_{name}"):
            raise FeatureOffError(name)

    return dependency


def meter_export(ctx: OwnerCtx) -> None:
    """Every export needs the plan's `exports` feature and counts toward the month's optional export limit."""
    with write_transaction() as session:
        entitlement_service.require_feature(session, ctx.shop_id, "exports")
        entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_EXPORTS)


def meter_image_analysis(ctx: Ctx) -> None:
    """Every photo analysis counts toward the month's optional limit (checked before any work is done)."""
    with write_transaction() as session:
        entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_IMAGE_ANALYSES)
