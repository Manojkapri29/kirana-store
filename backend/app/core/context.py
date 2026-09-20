"""Who is making a request. Every service call is scoped to `shop_id`."""

from dataclasses import dataclass

from app.models.enums import UserRole


@dataclass(frozen=True)
class RequestContext:
    shop_id: int
    user_id: int
    role: UserRole
