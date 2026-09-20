from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.common import Page
from app.services.supplier_service import SaveResult, SupplierView

# Only lengths are limited here. Phone, email and GSTIN formats are checked by the service, which gives
# clear per-field messages and stores each in one consistent form.
Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Gstin = Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    phone: Phone | None = None
    alternate_phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None
    gstin: Gstin | None = None
    notes: Notes | None = None


class SupplierUpdate(BaseModel):
    """Partial update: only the fields sent change. Send `null` (or empty text) to clear an optional one."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    phone: Phone | None = None
    alternate_phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None
    gstin: Gstin | None = None
    notes: Notes | None = None


class SupplierOut(BaseModel):
    id: int
    name: str
    phone: str | None
    alternate_phone: str | None
    email: str | None
    address: str | None
    gstin: str | None
    notes: str | None
    is_active: bool
    product_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: SupplierView) -> "SupplierOut":
        s = view.supplier
        return cls(
            id=s.id,
            name=s.name,
            phone=s.phone,
            alternate_phone=s.alternate_phone,
            email=s.email,
            address=s.address,
            gstin=s.gstin,
            notes=s.notes,
            is_active=s.is_active,
            product_count=view.product_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )


class SupplierSaved(BaseModel):
    supplier: SupplierOut
    warnings: list[str]

    @classmethod
    def from_result(cls, result: SaveResult) -> "SupplierSaved":
        return cls(supplier=SupplierOut.from_view(result.view), warnings=result.warnings)


class SupplierListOut(Page):
    items: list[SupplierOut]


class SupplierOptionOut(BaseModel):
    """Just enough to fill a picker."""

    id: int
    name: str
