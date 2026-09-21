"""Who is making a request. Every service call is scoped to `shop_id`."""

from dataclasses import dataclass

from app.core.permissions import ALL_PERMISSIONS, LEGACY_STAFF_PERMISSIONS
from app.models.enums import UserRole


@dataclass(frozen=True)
class RequestContext:
    """The shop, the membership (`user_id`) and what it may do, all worked out on the server from the session.

    `permissions` comes from the membership's role. A context built without it (tests, the development shortcut) falls
    back to what its coarse `role` implies; a real session always carries the role's permissions.
    """

    shop_id: int
    user_id: int
    role: UserRole
    permissions: frozenset[str] | None = None
    account_id: int | None = None
    session_id: int | None = None
    role_code: str | None = None

    @property
    def granted(self) -> frozenset[str]:
        if self.permissions is not None:
            return self.permissions
        return ALL_PERMISSIONS if self.role is UserRole.OWNER else LEGACY_STAFF_PERMISSIONS

    def has(self, permission: str) -> bool:
        return permission in self.granted

    @property
    def is_owner(self) -> bool:
        return self.role_code == "OWNER" if self.role_code is not None else self.role is UserRole.OWNER
