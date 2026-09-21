"""Authorising system administrators. Every admin route depends on `admin_permission(...)`.

One place does authentication (the `X-Admin-Token` header), the permission check for the role, rate limiting of failed
attempts per client address, and the audit row for the request (allowed or denied, with the route and the shop it targets). The
audit row is written in its own transaction BEFORE the outcome is raised, so a refusal is on record even though the request
itself fails. Nothing about a shop's own login or shop context is used here: an administrator has none.
"""

import logging
import re
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.core import observability, ratelimit
from app.db.session import write_transaction
from app.services import admin_service
from app.services.admin_service import AdminIdentity


def admin_permission(permission: str):  # noqa: ANN201
    def dependency(
        request: Request, x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None
    ) -> AdminIdentity:
        action = admin_action(request)
        shop_id = request.path_params.get("shop_id")
        target = int(shop_id) if isinstance(shop_id, str) and shop_id.isdigit() else None
        request_id = getattr(request.state, "correlation_id", None)
        address = request.client.host if request.client else "unknown"
        outcome = "ALLOWED"
        with write_transaction() as session:
            admin = admin_service.authenticate(session, x_admin_token)
            if admin is None:
                outcome = "UNAUTHENTICATED"
                admin_service.audit(session, None, action, permission=permission, outcome="DENIED", shop_id=target,
                                    detail={"reason": "authentication"}, request_id=request_id)  # fmt: skip
            elif not admin.can(permission):
                outcome = "FORBIDDEN"
                admin_service.audit(session, admin, action, permission=permission, outcome="DENIED", shop_id=target,
                                    detail={"reason": "permission"}, request_id=request_id)  # fmt: skip
            else:
                admin_service.audit(session, admin, action, permission=permission, outcome="ALLOWED", shop_id=target,
                                    detail={"query": sorted(request.query_params.keys())}, request_id=request_id)  # fmt: skip
        if outcome == "UNAUTHENTICATED":
            observability.log_event(
                "security", "admin authentication failed", level=logging.WARNING, endpoint=action
            )
            ratelimit.enforce("admin_auth", address)  # too many bad tokens from one address: 429
            raise HTTPException(status_code=401, detail="Administrator authentication is required.")
        assert admin is not None
        if outcome == "FORBIDDEN":
            observability.log_event(
                "security",
                "admin permission denied",
                level=logging.WARNING,
                endpoint=action,
                admin_id=admin.id,
            )
            raise HTTPException(status_code=403, detail="This administrator role does not allow that.")
        ratelimit.enforce("admin", f"admin{admin.id}")
        request.state.admin = admin
        return admin

    return dependency


def admin_action(request: Request) -> str:
    """ "METHOD /path" with record numbers masked (`/shops/{id}/status`), the same form the access log uses."""
    path = re.sub(r"/\d+(?=/|$)", "/{id}", request.url.path)
    return f"{request.method} {path}"
