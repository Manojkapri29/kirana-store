"""Authorization: what a member may do. The one place these questions are answered.

  * `permissions_of_role`  the permissions a role holds (read from `role_permissions`; OWNER always holds them all)
  * `require`              refuse (HTTP 403) unless the request's context holds every listed permission
  * `assignable_role`      a role a shop may give to a member (a system role, or that shop's own active custom role)
  * `check_grantable`      nobody can give away what they do not hold, and custom roles never carry owner-only permissions
  * `can_manage_member`    a member can only be managed by someone who holds at least what that member holds

The request path resolves the membership and role on the SERVER for every request (`auth_service.resolve_session`):
nothing here trusts a shop id or a role from the client, and nothing is cached across requests, so a change to a role, a
suspension or a removal takes effect on the very next call.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.permissions import ALL_PERMISSIONS, OWNER_ONLY_PERMISSIONS, PERMISSIONS
from app.models import Role, RolePermission, User
from app.services.errors import ForbiddenError, InvalidInputError, NotFoundError

DENIED = "You don't have permission to perform this action."
OWNER_CODE = "OWNER"


def permissions_of_role(session: Session, role: Role) -> frozenset[str]:
    if role.is_system and role.code == OWNER_CODE:
        return ALL_PERMISSIONS  # the owner is never locked out of a permission added later
    stored = session.scalars(select(RolePermission.permission).where(RolePermission.role_id == role.id))
    return frozenset(stored) & ALL_PERMISSIONS  # a code that no longer exists grants nothing


def permissions_of_roles(session: Session, roles: Iterable[Role]) -> dict[int, frozenset[str]]:
    """The permissions of several roles in ONE query (for lists: a screen of staff must not cost a query per person)."""
    roles = list(roles)
    grouped: dict[int, set[str]] = {r.id: set() for r in roles}
    ids = [r.id for r in roles if not (r.is_system and r.code == OWNER_CODE)]
    if ids:
        for role_id, permission in session.execute(
            select(RolePermission.role_id, RolePermission.permission).where(RolePermission.role_id.in_(ids))
        ):
            grouped[role_id].add(permission)
    return {
        r.id: ALL_PERMISSIONS
        if (r.is_system and r.code == OWNER_CODE)
        else frozenset(grouped[r.id]) & ALL_PERMISSIONS
        for r in roles
    }


def require(ctx: RequestContext, *permissions: str) -> None:
    missing = [p for p in permissions if not ctx.has(p)]
    if missing:
        raise ForbiddenError(DENIED, code="permission_denied")


def get_role(session: Session, shop_id: int, role_id: int) -> Role:
    """A role this shop may see: a system role or its own custom role. Another shop's role is "not found"."""
    role = session.scalar(
        select(Role).where(Role.id == role_id, (Role.shop_id.is_(None)) | (Role.shop_id == shop_id))
    )
    if role is None:
        raise NotFoundError("Role not found")
    return role


def assignable_role(session: Session, shop_id: int, role_id: int) -> Role:
    role = get_role(session, shop_id, role_id)
    if not role.is_active:
        raise InvalidInputError("That role is not in use any more. Choose another.", field="role_id")
    return role


def validate_permission_codes(codes: Iterable[str]) -> frozenset[str]:
    chosen = frozenset(codes)
    unknown = sorted(chosen - ALL_PERMISSIONS)
    if unknown:
        raise InvalidInputError(f"Unknown permission: {unknown[0]}.", field="permissions")
    return chosen


def check_custom_role_permissions(codes: Iterable[str]) -> frozenset[str]:
    chosen = validate_permission_codes(codes)
    owner_only = sorted(chosen & OWNER_ONLY_PERMISSIONS)
    if owner_only:
        raise InvalidInputError(
            f"{PERMISSIONS[owner_only[0]][1]} is reserved for the owner and cannot be given to a custom role.",
            field="permissions",
        )
    return chosen


def check_grantable(actor: RequestContext, permissions: frozenset[str]) -> None:
    """No privilege escalation: you can only hand out permissions you hold yourself."""
    beyond = sorted(permissions - actor.granted)
    if beyond:
        raise ForbiddenError(
            "You can only give permissions that you have yourself.", code="cannot_grant_beyond_own_access"
        )


def can_manage_member(actor: RequestContext, target: User, target_permissions: frozenset[str]) -> None:
    """A member can be managed by someone who holds everything they hold, and never by themselves (no self-promotion)."""
    if target.id == actor.user_id:
        raise ForbiddenError("You cannot change your own access.", code="cannot_manage_self")
    if not target_permissions <= actor.granted:
        raise ForbiddenError(
            "This member has access that you do not have, so only someone with more access can change it.",
            code="cannot_manage_higher_access",
        )
