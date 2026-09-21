"""Staff, invitations and roles. Who may call each route is in the central permission table (`core/permissions.py`);
the rules about WHOM a caller may manage (never yourself, never someone with more access) are in `staff_service`."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.core.config import get_settings
from app.db.session import get_session, write_transaction
from app.schemas.staff import (
    ChangeRoleIn,
    InvitationListOut,
    InvitationOut,
    InviteCreatedOut,
    InviteIn,
    PermissionOut,
    RoleCreate,
    RoleListOut,
    RoleOut,
    RoleRef,
    RoleUpdate,
    StaffListOut,
    StaffOut,
)
from app.services import invitation_service, staff_service

ReadSession = Annotated[Session, Depends(get_session)]
staff = APIRouter(prefix="/staff", tags=["staff"])
roles = APIRouter(prefix="/roles", tags=["roles"])


def _staff(member: staff_service.Member, ctx: Ctx, *, detail: bool = False) -> StaffOut:
    user, role = member.user, member.role
    return StaffOut(
        id=user.id, name=user.full_name, email=user.email,
        role=RoleRef(id=role.id, code=role.code, name=role.name, is_system=role.is_system) if role else None,
        status=user.status.value, joined_at=user.joined_at, last_active_at=user.last_active_at,
        permission_count=len(member.permissions), is_you=user.id == ctx.user_id,
        permissions=sorted(member.permissions) if detail else None,
    )  # fmt: skip


def _invitation(invitation, role_name: str) -> InvitationOut:  # noqa: ANN001
    return InvitationOut(
        id=invitation.id, email=invitation.email, role=role_name, status=invitation_service.effective_status(invitation).value,
        expires_at=invitation.expires_at, created_at=invitation.created_at,
    )  # fmt: skip


def _role(view: staff_service.RoleView) -> RoleOut:
    r = view.role
    return RoleOut(
        id=r.id,
        code=r.code,
        name=r.name,
        description=r.description,
        is_system=r.is_system,
        is_active=r.is_active,
        permissions=sorted(view.permissions),
        members=view.members,
    )


# --- Staff --------------------------------------------------------------------------------------------------------------------


@staff.get("", response_model=StaffListOut, summary="Everyone in this shop with their role and status")
def list_staff(ctx: Ctx, session: ReadSession, include_removed: bool = False) -> StaffListOut:
    members = staff_service.list_members(session, ctx.shop_id, include_removed=include_removed)
    return StaffListOut(items=[_staff(m, ctx) for m in members], total=len(members))


@staff.get("/invitations", response_model=InvitationListOut, summary="Invitations sent from this shop")
def list_invitations(ctx: Ctx, session: ReadSession) -> InvitationListOut:
    return InvitationListOut(
        items=[_invitation(i, r.name) for i, r in invitation_service.list_invitations(session, ctx.shop_id)]
    )


@staff.post(
    "/invitations",
    response_model=InviteCreatedOut,
    status_code=201,
    summary="Invite someone to join with a role",
)
def invite(payload: InviteIn, ctx: Ctx) -> InviteCreatedOut:
    with write_transaction() as session:
        created = invitation_service.create(session, ctx, payload.email, payload.role_id)
        role_name = staff_service.get_role_view(session, ctx.shop_id, created.invitation.role_id).role.name
        out = _invitation(created.invitation, role_name)
    base = (get_settings().frontend_url or "").rstrip("/")
    return InviteCreatedOut(invitation=out, link=f"{base}/accept-invitation#token={created.token}")


@staff.post(
    "/invitations/{invitation_id}/revoke", response_model=InvitationOut, summary="Withdraw an invitation"
)
def revoke_invitation(invitation_id: int, ctx: Ctx) -> InvitationOut:
    with write_transaction() as session:
        invitation = invitation_service.revoke(session, ctx, invitation_id)
        return _invitation(
            invitation, staff_service.get_role_view(session, ctx.shop_id, invitation.role_id).role.name
        )


@staff.get("/{member_id}", response_model=StaffOut, summary="One staff member, with what they may do")
def get_member(member_id: int, ctx: Ctx, session: ReadSession) -> StaffOut:
    return _staff(staff_service.get_member(session, ctx.shop_id, member_id), ctx, detail=True)


@staff.patch("/{member_id}", response_model=StaffOut, summary="Change a staff member's role")
def change_role(member_id: int, payload: ChangeRoleIn, ctx: Ctx) -> StaffOut:
    with write_transaction() as session:
        return _staff(staff_service.change_role(session, ctx, member_id, payload.role_id), ctx, detail=True)


@staff.post(
    "/{member_id}/suspend",
    response_model=StaffOut,
    summary="Suspend a staff member (their access stops at once)",
)
def suspend(member_id: int, ctx: Ctx) -> StaffOut:
    with write_transaction() as session:
        staff_service.suspend(session, ctx, member_id)
        return _staff(staff_service.get_member(session, ctx.shop_id, member_id), ctx)


@staff.post(
    "/{member_id}/reactivate", response_model=StaffOut, summary="Let a suspended staff member back in"
)
def reactivate(member_id: int, ctx: Ctx) -> StaffOut:
    with write_transaction() as session:
        staff_service.reactivate(session, ctx, member_id)
        return _staff(staff_service.get_member(session, ctx.shop_id, member_id), ctx)


@staff.post(
    "/{member_id}/remove", response_model=StaffOut, summary="Remove a staff member (their history is kept)"
)
def remove(member_id: int, ctx: Ctx) -> StaffOut:
    with write_transaction() as session:
        staff_service.remove(session, ctx, member_id)
        return _staff(staff_service.get_member(session, ctx.shop_id, member_id), ctx)


# --- Roles --------------------------------------------------------------------------------------------------------------------


@roles.get("", response_model=RoleListOut, summary="System roles and this shop's own roles")
def list_roles(ctx: Ctx, session: ReadSession) -> RoleListOut:
    return RoleListOut(items=[_role(v) for v in staff_service.list_roles(session, ctx.shop_id)])


@roles.get("/permissions", response_model=list[PermissionOut], summary="Every permission there is, grouped")
def permissions(ctx: Ctx) -> list[PermissionOut]:
    return [PermissionOut(**p) for p in staff_service.catalogue()]  # type: ignore[arg-type]


@roles.get("/{role_id}", response_model=RoleOut, summary="One role and its permissions")
def get_role(role_id: int, ctx: Ctx, session: ReadSession) -> RoleOut:
    return _role(staff_service.get_role_view(session, ctx.shop_id, role_id))


@roles.post("", response_model=RoleOut, status_code=201, summary="Create a custom role")
def create_role(payload: RoleCreate, ctx: Ctx) -> RoleOut:
    with write_transaction() as session:
        return _role(
            staff_service.create_role(session, ctx, payload.name, payload.description, payload.permissions)
        )


@roles.patch("/{role_id}", response_model=RoleOut, summary="Rename a custom role or change its permissions")
def update_role(role_id: int, payload: RoleUpdate, ctx: Ctx) -> RoleOut:
    with write_transaction() as session:
        return _role(
            staff_service.update_role(
                session,
                ctx,
                role_id,
                name=payload.name,
                description=payload.description,
                permissions=payload.permissions,
            )
        )


@roles.post(
    "/{role_id}/deactivate",
    response_model=RoleOut,
    summary="Retire a custom role (refused while staff use it)",
)
def deactivate_role(role_id: int, ctx: Ctx) -> RoleOut:
    with write_transaction() as session:
        return _role(staff_service.set_role_active(session, ctx, role_id, False))


@roles.post("/{role_id}/reactivate", response_model=RoleOut, summary="Bring a retired custom role back")
def reactivate_role(role_id: int, ctx: Ctx) -> RoleOut:
    with write_transaction() as session:
        return _role(staff_service.set_role_active(session, ctx, role_id, True))
