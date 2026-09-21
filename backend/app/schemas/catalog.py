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


class BusinessTypeOut(BaseModel):
    code: str
    name: str


class SuggestedCategoryOut(BaseModel):
    name: str
    exists: bool


class ShopTemplateOut(BaseModel):
    """Defaults suggested by the shop's business type. Suggestions only: nothing here restricts the shop."""

    business_type: str
    business_type_name: str
    categories: list[SuggestedCategoryOut]
    unit_codes: list[str]


class ShopUpdate(BaseModel):
    """The shop settings that can be changed. Only the business type for now."""

    model_config = ConfigDict(extra="forbid")
    business_type: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None


class ShopOut(BaseModel):
    id: int
    name: str  # the business name
    business_type: str
    business_type_name: str
    timezone: str
    language: Language
    allow_negative_stock: bool
    mrp_validation_mode: MrpValidationMode
    upi_id: str | None = None  # shown on the billing screen when a customer pays by UPI (read-only for now)
