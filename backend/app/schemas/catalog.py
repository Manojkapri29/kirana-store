from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.enums import Language, MrpValidationMode

Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class UnitOut(BaseModel):
    id: int
    code: str
    name: str
    allows_decimal: bool


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    is_active: bool | None = None


class CategoryOut(BaseModel):
    id: int
    name: str
    is_active: bool


class ShopOut(BaseModel):
    id: int
    name: str
    timezone: str
    language: Language
    allow_negative_stock: bool
    mrp_validation_mode: MrpValidationMode
