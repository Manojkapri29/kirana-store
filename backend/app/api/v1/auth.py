"""Sign in and out, the current session, choosing a shop, changing a password, and accepting an invitation.

These routes do not act inside a shop, so they are outside the per-route permission table (`UNGUARDED_PREFIXES`).
The session token lives in an HttpOnly cookie; the CSRF token is readable by the app and must be sent back in a header on
every state-changing call made with the cookie.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import Principal, client_address
from app.core.config import get_settings
from app.core.permissions import ALL_PERMISSIONS
from app.db.session import write_transaction
from app.schemas.auth import (
    AcceptInvitationIn,
    AccountOut,
    ChangePasswordIn,
    InvitationPreviewIn,
    InvitationPreviewOut,
    LoginIn,
    MembershipOut,
    PasswordChangedOut,
    SelectShopIn,
    SessionOut,
)
from app.services import auth_service, invitation_service
from app.services.errors import AuthenticationError, RateLimitedError

router = APIRouter(prefix="/auth", tags=["authentication"])


def _set_cookies(response: Response, token: str, csrf: str, max_age: int) -> None:
    settings = get_settings()
    common = {
        "max_age": max_age,
        "secure": settings.cookie_secure,
        "samesite": settings.session_cookie_samesite,
        "path": "/",
    }
    response.set_cookie(settings.session_cookie_name, token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(settings.csrf_cookie_name, csrf, httponly=False, **common)  # type: ignore[arg-type]


def _clear_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, samesite=settings.session_cookie_samesite
        )


def _memberships(items: list[auth_service.MembershipView]) -> list[MembershipOut]:
    return [
        MembershipOut(
            user_id=m.user_id,
            shop_id=m.shop_id,
            shop_name=m.shop_name,
            role_code=m.role_code,
            role_name=m.role_name,
            status=m.status.value,
        )
        for m in items
    ]


def _session_out(
    resolved: auth_service.Resolved,
    memberships: list[auth_service.MembershipView],
    *,
    csrf: str | None = None,
    token: str | None = None,
) -> SessionOut:
    settings = get_settings()
    active = next(
        (m for m in memberships if resolved.user is not None and m.user_id == resolved.user.id), None
    )
    return SessionOut(
        account=AccountOut(
            id=resolved.account.id, email=resolved.account.email, full_name=resolved.account.full_name
        ),
        memberships=_memberships(memberships),
        active_user_id=active.user_id if active else None,
        shop_id=active.shop_id if active else None,
        shop_name=active.shop_name if active else None,
        role_code=active.role_code if active else None,
        role_name=active.role_name if active else None,
        permissions=sorted(resolved.permissions & ALL_PERMISSIONS),
        expires_at=resolved.session.expires_at,
        idle_minutes=settings.session_idle_minutes,
        csrf_token=csrf,
        token=token,
    )


def _finish_login(response: Response, result: auth_service.LoginResult, issue_token: bool) -> SessionOut:
    settings = get_settings()
    _set_cookies(response, result.token, result.csrf_token, settings.session_absolute_hours * 3600)
    with write_transaction() as session:  # read back through the same resolution every request uses
        resolved = auth_service.resolve_session(session, result.token)
        assert resolved is not None
        session.expunge_all()
    return _session_out(
        resolved, result.memberships, csrf=result.csrf_token, token=result.token if issue_token else None
    )


@router.post("/login", response_model=SessionOut, summary="Sign in with an email and password")
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    issue_token: Annotated[
        bool, Query(description="Also return a Bearer token (for non-browser clients)")
    ] = False,
) -> SessionOut:
    failure: AuthenticationError | RateLimitedError | None = None
    result = None
    with (
        write_transaction() as session
    ):  # committed even when the sign-in fails: the failed-attempt count must persist
        try:
            result = auth_service.login(
                session, payload.email, payload.password, client_address=client_address(request)
            )
        except (AuthenticationError, RateLimitedError) as exc:
            failure = exc
    if failure is not None:
        raise failure
    assert result is not None
    return _finish_login(response, result, issue_token)


@router.get("/me", response_model=SessionOut, summary="Who is signed in, which shop, and what they may do")
def me(resolved: Principal) -> SessionOut:
    from app.db.session import read_session

    with read_session() as session:
        memberships = auth_service.memberships_of(session, resolved.account.id)
    return _session_out(resolved, memberships)


@router.post("/select-shop", response_model=SessionOut, summary="Use one of your shops for this session")
def select_shop(payload: SelectShopIn, response: Response, resolved: Principal) -> SessionOut:
    with write_transaction() as session:
        auth_service.select_shop(session, resolved, payload.user_id)
        fresh = auth_service.resolve_row(session, resolved.session.id)
        memberships = auth_service.memberships_of(session, resolved.account.id)
        assert fresh is not None
        session.expunge_all()
    return _session_out(fresh, memberships)


@router.post("/logout", status_code=204, summary="Sign out (this session ends at once)")
def logout(response: Response, resolved: Principal) -> None:
    with write_transaction() as session:
        auth_service.revoke(session, resolved)
    _clear_cookies(response)


@router.post(
    "/change-password",
    response_model=PasswordChangedOut,
    summary="Change your password (your other sessions end)",
)
def change_password(payload: ChangePasswordIn, resolved: Principal) -> PasswordChangedOut:
    with write_transaction() as session:
        ended = auth_service.change_password(
            session, resolved, payload.current_password, payload.new_password
        )
    return PasswordChangedOut(other_sessions_ended=ended)


@router.post(
    "/invitations/preview",
    response_model=InvitationPreviewOut,
    summary="What an invitation is for (before accepting)",
)
def preview_invitation(payload: InvitationPreviewIn, request: Request) -> InvitationPreviewOut:
    with write_transaction() as session:
        p = invitation_service.preview(session, payload.token, client_address=client_address(request))
    return InvitationPreviewOut(
        shop_name=p.shop_name,
        email=p.email,
        role_name=p.role_name,
        has_account=p.has_account,
        expires_at=p.expires_at,
    )


@router.post("/invitations/accept", response_model=SessionOut, summary="Accept an invitation and sign in")
def accept_invitation(payload: AcceptInvitationIn, request: Request, response: Response) -> SessionOut:
    failure: AuthenticationError | RateLimitedError | None = None
    result = None
    with write_transaction() as session:
        try:
            result = invitation_service.accept(
                session,
                payload.token,
                password=payload.password,
                full_name=payload.full_name,
                client_address=client_address(request),
            )
        except (AuthenticationError, RateLimitedError) as exc:
            failure = exc
    if failure is not None:
        raise failure
    assert result is not None
    return _finish_login(response, result, False)
