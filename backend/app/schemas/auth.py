from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
Password = Annotated[
    str, StringConstraints(min_length=1, max_length=256)
]  # length rules are the service's (with a clear message)
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
InviteToken = Annotated[str, StringConstraints(min_length=20, max_length=200)]


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    password: Password


class SelectShopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int  # the membership to use; it must be one of the caller's own


class ChangePasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Password
    new_password: Password


class AcceptInvitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: InviteToken
    password: Password
    full_name: PersonName | None = None


class InvitationPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: InviteToken


class MembershipOut(BaseModel):
    user_id: int
    shop_id: int
    shop_name: str
    role_code: str
    role_name: str
    status: str


class AccountOut(BaseModel):
    id: int
    email: str
    full_name: str


class SessionOut(BaseModel):
    """What the app needs to draw itself. Permissions are for hiding buttons only: the server checks every call."""

    account: AccountOut
    memberships: list[MembershipOut]
    active_user_id: int | None
    shop_id: int | None
    shop_name: str | None
    role_code: str | None
    role_name: str | None
    permissions: list[str]
    expires_at: datetime  # the absolute end of this session
    idle_minutes: int  # signed out after this long without use
    csrf_token: str | None = None
    token: str | None = None  # only when a non-browser client asked for a Bearer token


class InvitationPreviewOut(BaseModel):
    shop_name: str
    email: str
    role_name: str
    has_account: bool  # if so the person signs in with their existing password to accept
    expires_at: datetime


class PasswordChangedOut(BaseModel):
    other_sessions_ended: int
