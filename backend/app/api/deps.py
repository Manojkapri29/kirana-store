"""Request dependencies shared by all routers."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.session import read_session
from app.models.enums import UserRole
from app.services import context_service


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
    return context


Ctx = Annotated[RequestContext, Depends(_context_for_request)]


def require_owner(ctx: Ctx) -> RequestContext:
    if ctx.role is not UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Only the shop owner can do this.")
    return ctx


OwnerCtx = Annotated[RequestContext, Depends(require_owner)]
