"""Staff and roles: who is in a shop, what each person may do, and the rules that keep that safe.

  * a person's membership is never deleted: REMOVED keeps the history (their name stays on what they did)
  * nobody changes their own access, and nobody manages a member who holds more than they do
  * nobody gives away a permission they do not hold (no escalating yourself through a role or an invitation)
  * a shop always has at least one active owner
  * a custom role cannot hold an owner-only permission, and cannot be retired while members use it
Every change is written to the shop's audit log with who did it and to whom. Services never commit.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.permissions import ALL_PERMISSIONS, PERMISSIONS
from app.db.types import utc_now
from app.models import AuthSession, Role, RolePermission, User
from app.models.enums import MembershipStatus, UserRole
from app.services import audit_service, authorization_service
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

OWNER_CODE = authorization_service.OWNER_CODE
_LIVE = (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED, MembershipStatus.INVITED)


@dataclass(frozen=True)
class Member:
    user: User
    role: Role | None
    permissions: frozenset[str]


def _member(session: Session, user: User) -> Member:
    role = session.get(Role, user.role_id) if user.role_id else None
    return Member(
        user, role, authorization_service.permissions_of_role(session, role) if role else frozenset()
    )


def list_members(session: Session, shop_id: int, *, include_removed: bool = False) -> list[Member]:
    query = select(User).where(User.shop_id == shop_id).order_by(User.status, User.full_name, User.id)
    if not include_removed:
        query = query.where(User.status != MembershipStatus.REMOVED)
    users = list(session.scalars(query))
    role_ids = {u.role_id for u in users if u.role_id}
    roles = {r.id: r for r in session.scalars(select(Role).where(Role.id.in_(role_ids)))} if role_ids else {}
    perms = authorization_service.permissions_of_roles(session, roles.values())
    return [
        Member(
            u,
            roles.get(u.role_id) if u.role_id else None,
            perms.get(u.role_id, frozenset()) if u.role_id else frozenset(),
        )
        for u in users
    ]


def get_member(session: Session, shop_id: int, member_id: int) -> Member:
    user = session.scalar(select(User).where(User.id == member_id, User.shop_id == shop_id))
    if user is None:
        raise NotFoundError("Staff member not found")
    return _member(session, user)


def _active_owners(session: Session, shop_id: int) -> int:
    return session.scalar(
        select(func.count()).select_from(User).join(Role, Role.id == User.role_id)
        .where(User.shop_id == shop_id, User.status == MembershipStatus.ACTIVE, Role.is_system.is_(True), Role.code == OWNER_CODE)
    ) or 0  # fmt: skip


def _is_owner(member: Member) -> bool:
    return member.role is not None and member.role.is_system and member.role.code == OWNER_CODE


def _end_sessions(session: Session, user_id: int, reason: str) -> None:
    session.execute(
        update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now(), revoked_reason=reason)
    )  # fmt: skip


def change_role(session: Session, ctx: RequestContext, member_id: int, role_id: int) -> Member:
    target = session.scalar(
        select(User).where(User.id == member_id, User.shop_id == ctx.shop_id).with_for_update()
    )
    if target is None:
        raise NotFoundError("Staff member not found")
    current = _member(session, target)
    authorization_service.can_manage_member(ctx, target, current.permissions)
    if target.status is MembershipStatus.REMOVED:
        raise ConflictError("This person has been removed. Invite them again to give them access.")
    role = authorization_service.assignable_role(session, ctx.shop_id, role_id)
    new_permissions = authorization_service.permissions_of_role(session, role)
    authorization_service.check_grantable(ctx, new_permissions)
    if (
        _is_owner(current)
        and role.code != OWNER_CODE
        and target.status is MembershipStatus.ACTIVE
        and _active_owners(session, ctx.shop_id) <= 1
    ):
        raise ConflictError("A shop must always have an owner. Make someone else an owner first.")
    before = {"role": current.role.code if current.role else None}
    target.role_id = role.id
    target.role = UserRole.OWNER if role.is_system and role.code == OWNER_CODE else UserRole.STAFF
    audit_service.record_access_event(
        session, ctx.shop_id, ctx.user_id, "role_changed", "user", target.id,
        {**before, "to": role.code, "permissions_added": sorted(new_permissions - current.permissions), "permissions_removed": sorted(current.permissions - new_permissions)},
    )  # fmt: skip
    return Member(target, role, new_permissions)


def _set_status(
    session: Session,
    ctx: RequestContext,
    member_id: int,
    allowed_from: tuple[MembershipStatus, ...],
    to: MembershipStatus,
    action: str,
) -> Member:
    target = session.scalar(
        select(User).where(User.id == member_id, User.shop_id == ctx.shop_id).with_for_update()
    )
    if target is None:
        raise NotFoundError("Staff member not found")
    current = _member(session, target)
    authorization_service.can_manage_member(ctx, target, current.permissions)
    if target.status not in allowed_from:
        raise ConflictError("That is not possible for this person right now.")
    if (
        to in (MembershipStatus.SUSPENDED, MembershipStatus.REMOVED)
        and _is_owner(current)
        and target.status is MembershipStatus.ACTIVE
        and _active_owners(session, ctx.shop_id) <= 1
    ):
        raise ConflictError("A shop must always have an owner. Make someone else an owner first.")
    before = target.status.value
    target.status = to
    target.is_active = to is MembershipStatus.ACTIVE
    if to is MembershipStatus.REMOVED:
        target.removed_at = utc_now()
    if to is MembershipStatus.ACTIVE:
        target.removed_at = None
    if to is not MembershipStatus.ACTIVE:
        _end_sessions(session, target.id, "membership_" + to.value.lower())
    audit_service.record_access_event(
        session, ctx.shop_id, ctx.user_id, action, "user", target.id, {"from": before, "to": to.value}
    )
    return current


def suspend(session: Session, ctx: RequestContext, member_id: int) -> Member:
    return _set_status(
        session, ctx, member_id, (MembershipStatus.ACTIVE,), MembershipStatus.SUSPENDED, "staff_suspended"
    )


def reactivate(session: Session, ctx: RequestContext, member_id: int) -> Member:
    return _set_status(
        session, ctx, member_id, (MembershipStatus.SUSPENDED,), MembershipStatus.ACTIVE, "staff_reactivated"
    )


def remove(session: Session, ctx: RequestContext, member_id: int) -> Member:
    return _set_status(
        session,
        ctx,
        member_id,
        (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED, MembershipStatus.INVITED),
        MembershipStatus.REMOVED,
        "membership_removed",
    )


# --- Roles ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleView:
    role: Role
    permissions: frozenset[str]
    members: int


def list_roles(session: Session, shop_id: int) -> list[RoleView]:
    roles = list(
        session.scalars(
            select(Role)
            .where((Role.shop_id.is_(None)) | (Role.shop_id == shop_id))
            .order_by(Role.is_system.desc(), Role.name)
        )
    )
    counts = dict(
        session.execute(
            select(User.role_id, func.count())
            .where(User.shop_id == shop_id, User.status.in_(_LIVE))
            .group_by(User.role_id)
        ).all()
    )
    perms = authorization_service.permissions_of_roles(session, roles)
    return [RoleView(r, perms[r.id], int(counts.get(r.id, 0))) for r in roles]


def get_role_view(session: Session, shop_id: int, role_id: int) -> RoleView:
    role = authorization_service.get_role(session, shop_id, role_id)
    members = (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.shop_id == shop_id, User.role_id == role.id, User.status.in_(_LIVE))
        )
        or 0
    )
    return RoleView(role, authorization_service.permissions_of_role(session, role), int(members))


def _code_for(session: Session, shop_id: int, name: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:30] or "ROLE"
    code, n = f"CUSTOM_{base}", 1
    while session.scalar(select(Role.id).where(Role.shop_id == shop_id, Role.code == code)) is not None:
        n += 1
        code = f"CUSTOM_{base}_{n}"
    return code


def _name_free(session: Session, shop_id: int, name: str, except_id: int | None = None) -> None:
    query = select(Role.id).where(
        func.lower(Role.name) == name.lower(), (Role.shop_id.is_(None)) | (Role.shop_id == shop_id)
    )
    if except_id is not None:
        query = query.where(Role.id != except_id)
    if session.scalar(query) is not None:
        raise ConflictError("A role with this name already exists.", field="name")


def create_role(
    session: Session, ctx: RequestContext, name: str, description: str | None, permissions: list[str]
) -> RoleView:
    name = name.strip()
    if not name:
        raise InvalidInputError("Give the role a name.", field="name")
    chosen = authorization_service.check_custom_role_permissions(permissions)
    if not chosen:
        raise InvalidInputError("Choose at least one permission.", field="permissions")
    authorization_service.check_grantable(ctx, chosen)
    _name_free(session, ctx.shop_id, name)
    role = Role(
        shop_id=ctx.shop_id,
        code=_code_for(session, ctx.shop_id, name),
        name=name,
        description=(description or None),
        is_system=False,
        is_active=True,
    )
    session.add(role)
    session.flush()
    session.add_all(RolePermission(role_id=role.id, permission=p) for p in sorted(chosen))
    audit_service.record_access_event(
        session,
        ctx.shop_id,
        ctx.user_id,
        "role_created",
        "role",
        role.id,
        {"name": name, "permissions": sorted(chosen)},
    )
    return RoleView(role, chosen, 0)


def _custom_role(session: Session, ctx: RequestContext, role_id: int) -> Role:
    role = session.scalar(
        select(Role).where(Role.id == role_id, Role.shop_id == ctx.shop_id).with_for_update()
    )
    if role is None:
        raise NotFoundError("Role not found")  # system roles and other shops' roles cannot be edited here
    return role


def update_role(
    session: Session,
    ctx: RequestContext,
    role_id: int,
    *,
    name: str | None,
    description: str | None,
    permissions: list[str] | None,
) -> RoleView:
    role = _custom_role(session, ctx, role_id)
    before = authorization_service.permissions_of_role(session, role)
    if name is not None:
        name = name.strip()
        if not name:
            raise InvalidInputError("Give the role a name.", field="name")
        _name_free(session, ctx.shop_id, name, except_id=role.id)
        role.name = name
    if description is not None:
        role.description = description or None
    after = before
    if permissions is not None:
        after = authorization_service.check_custom_role_permissions(permissions)
        if not after:
            raise InvalidInputError("Choose at least one permission.", field="permissions")
        authorization_service.check_grantable(ctx, after)
        # You cannot quietly change a role you hold yourself: that would be editing your own access.
        me = session.get(User, ctx.user_id)
        if me is not None and me.role_id == role.id:
            raise InvalidInputError(
                "You cannot change the permissions of the role you hold.", field="permissions"
            )
        if after != before:
            session.execute(RolePermission.__table__.delete().where(RolePermission.role_id == role.id))
            session.add_all(RolePermission(role_id=role.id, permission=p) for p in sorted(after))
    audit_service.record_access_event(
        session, ctx.shop_id, ctx.user_id, "permission_changed" if after != before else "role_updated", "role", role.id,
        {"permissions_added": sorted(after - before), "permissions_removed": sorted(before - after)},
    )  # fmt: skip
    return get_role_view(session, ctx.shop_id, role.id)


def set_role_active(session: Session, ctx: RequestContext, role_id: int, active: bool) -> RoleView:
    role = _custom_role(session, ctx, role_id)
    if not active:
        using = (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.shop_id == ctx.shop_id, User.role_id == role.id, User.status.in_(_LIVE))
            )
            or 0
        )
        if using:
            raise ConflictError(
                f"{using} staff member(s) still use this role. Move them to another role first."
            )
    role.is_active = active
    audit_service.record_access_event(
        session,
        ctx.shop_id,
        ctx.user_id,
        "role_reactivated" if active else "role_deactivated",
        "role",
        role.id,
        None,
    )
    return get_role_view(session, ctx.shop_id, role.id)


def catalogue() -> list[dict[str, str | bool]]:
    from app.core.permissions import OWNER_ONLY_PERMISSIONS

    return [
        {"code": c, "group": g, "description": d, "owner_only": c in OWNER_ONLY_PERMISSIONS}
        for c, (g, d) in PERMISSIONS.items()
    ]


__all__ = ["ALL_PERMISSIONS"]
