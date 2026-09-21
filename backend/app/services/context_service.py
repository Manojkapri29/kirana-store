"""Resolves the current shop and user for a request.

The real context comes from the signed-in session (`api/deps.get_request_context`). This is the development
shortcut (`KIRANA_DEV_AUTH_BYPASS`): the user created by `python -m app.seed`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.dev import DEV_USER_EMAIL
from app.models import User


def resolve_development_context(session: Session) -> RequestContext | None:
    user = session.scalar(select(User).where(User.email == DEV_USER_EMAIL, User.is_active.is_(True)))
    if user is None:
        return None
    return RequestContext(shop_id=user.shop_id, user_id=user.id, role=user.role)
