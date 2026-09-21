from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import Page

RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class RoleRef(BaseModel):
    id: int
    code: str
    name: str
    is_system: bool


class StaffOut(BaseModel):
    id: int  # the membership id (what every "who did it" refers to)
    name: str
    email: str
    role: RoleRef | None
    status: str
    joined_at: datetime | None
    last_active_at: datetime | None
    permission_count: int
    is_you: bool
    permissions: list[str] | None = None  # on the detail view


class StaffListOut(BaseModel):
    items: list[StaffOut]
    total: int


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
    role_id: int


class InvitationOut(BaseModel):
    id: int
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime


class InviteCreatedOut(BaseModel):
    invitation: InvitationOut
    link: str  # shown ONCE: only its hash is stored. No email is sent; pass it to the person yourself.
    delivery: str = "not_sent"


class InvitationListOut(BaseModel):
    items: list[InvitationOut]


class ChangeRoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: int


class RoleOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    is_active: bool
    permissions: list[str]
    members: int


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RoleName
    description: Description | None = None
    permissions: list[str] = Field(min_length=1, max_length=100)


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RoleName | None = None
    description: Description | None = None
    permissions: list[str] | None = Field(default=None, min_length=1, max_length=100)


class PermissionOut(BaseModel):
    code: str
    group: str
    description: str
    owner_only: bool


class RoleListOut(BaseModel):
    items: list[RoleOut]


_ = Page
