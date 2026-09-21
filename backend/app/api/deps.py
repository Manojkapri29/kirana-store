"""Request dependencies shared by all routers."""

import re
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.core import permissions, ratelimit
from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.session import read_session, write_transaction
from app.models.enums import UserRole
from app.services import (
    account_service,
    audit_service,
    auth_service,
    authorization_service,
    context_service,
    entitlement_service,
)
from app.services.errors import FeatureOffError, ForbiddenError


def client_address(request: Request) -> str:
    """The caller's address for rate limiting. X-Forwarded-For is believed only when the operator says a proxy sets it."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()[
                :64
            ]  # the address the trusted proxy appended, not one the client claims
    return request.client.host if request.client else "unknown"


def session_token(request: Request) -> tuple[str | None, bool]:
    """The session token and whether it came from the cookie (cookies need CSRF protection; Bearer tokens do not)."""
    settings = get_settings()
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None, False
    cookie = request.cookies.get(settings.session_cookie_name)
    return cookie, True


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_principal(request: Request) -> auth_service.Resolved:
    """The signed-in account behind the request (no shop needed). 401 when there is no valid session; 403 on a bad CSRF token."""
    token, from_cookie = session_token(request)
    with read_session() as session:
        resolved = auth_service.resolve_session(session, token)
        if resolved is not None:
            session.expunge_all()  # keep the loaded values after the session closes
    if resolved is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    if from_cookie and request.method in UNSAFE_METHODS:
        if not auth_service.csrf_matches(resolved.session, request.headers.get("x-csrf-token")):
            raise ForbiddenError(
                "Your session could not be verified. Please refresh the page and try again.", code="csrf"
            )
    if auth_service.needs_touch(resolved.session):
        with write_transaction() as session:
            auth_service.touch(session, resolved.session.id, resolved.user.id if resolved.user else None)
    request.state.account_id = resolved.account.id
    return resolved


Principal = Annotated[auth_service.Resolved, Depends(get_principal)]


def _development_context() -> RequestContext:
    with read_session() as session:
        context = context_service.resolve_development_context(session)
    if context is None:
        raise HTTPException(status_code=503, detail="Development user not found. Run: python -m app.seed")
    return context


def get_request_context(request: Request) -> RequestContext:
    """The shop and membership making the request, worked out on the server from the session.

    Nothing the client sends (a shop id in a body, a query string or a header) decides this. The session names a
    membership; the membership names the shop and the role; the role names the permissions. If the membership has been
    suspended or removed since the session began, the request is refused. `KIRANA_DEV_AUTH_BYPASS` (never in production)
    keeps the old "act as the seeded owner" shortcut for local work.
    """
    settings = get_settings()
    if settings.dev_auth_bypass and not settings.is_production:
        return _development_context()
    resolved = get_principal(request)
    if resolved.user is None or resolved.role is None:
        if resolved.session.user_id is not None:
            raise ForbiddenError("Your access to this shop is not active.", code="membership_inactive")
        with read_session() as session:
            has_shop = bool(auth_service.memberships_of(session, resolved.account.id))
        if not has_shop:
            raise ForbiddenError("You do not have access to any shop right now.", code="no_active_membership")
        raise ForbiddenError("Choose a shop to continue.", code="shop_not_selected")
    user, role = resolved.user, resolved.role
    return RequestContext(
        shop_id=user.shop_id,
        user_id=user.id,
        role=UserRole.OWNER
        if role.code == authorization_service.OWNER_CODE and role.is_system
        else UserRole.STAFF,
        permissions=resolved.permissions,
        account_id=resolved.account.id,
        session_id=resolved.session.id,
        role_code=role.code,
    )


def _context_for_request(
    request: Request, context: Annotated[RequestContext, Depends(get_request_context)]
) -> RequestContext:
    """The request's context, after the one central permission check, also remembered on the request so error
    diagnostics can name the shop and user (ids only: never a name or a contact detail)."""
    request.state.shop_id = context.shop_id
    request.state.user_id = context.user_id
    path = request.url.path.removeprefix("/api/v1")
    rule = permissions.rule_for(request.method, path)
    if rule is None:
        # A route nobody wrote a rule for is refused, not allowed: fail closed. (A test lists every route.)
        raise ForbiddenError(authorization_service.DENIED, code="no_permission_rule")
    if rule.needs != (permissions.MEMBER,):
        authorization_service.require(context, *rule.needs)
    # The shop's account state decides what it may do (suspended and deactivated shops are restricted by policy).
    with read_session() as session:
        blocked = account_service.restriction(session, context.shop_id, request.method, request.url.path)
    if blocked is not None:
        raise blocked
    return context


Ctx = Annotated[RequestContext, Depends(_context_for_request)]


def require_owner(ctx: Ctx) -> RequestContext:
    """For an action that only an owner may take, whatever permissions a role was given."""
    if not ctx.is_owner:
        raise ForbiddenError("Only the shop owner can do this.", code="owner_only")
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


def meter_export(ctx: Ctx, request: Request) -> None:
    """Every export needs the plan's `exports` feature and counts toward the month's optional export limit. Each one is
    written to the audit log (who downloaded which file): a data export is a sensitive act."""
    with write_transaction() as session:
        entitlement_service.require_feature(session, ctx.shop_id, "exports")
        entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_EXPORTS)
        audit_service.record_access_event(
            session, ctx.shop_id, ctx.user_id, "export", "export", None,
            {"file": re.sub(r"/\d+(?=/|$)", "/{id}", request.url.path).removeprefix("/api/v1/exports/")},
        )  # fmt: skip


def meter_image_analysis(ctx: Ctx) -> None:
    """Every photo analysis counts toward the month's optional limit (checked before any work is done)."""
    with write_transaction() as session:
        entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_IMAGE_ANALYSES)
