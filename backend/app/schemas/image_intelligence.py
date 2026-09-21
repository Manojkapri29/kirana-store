from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import MoneyIn, QuantityIn
from app.schemas.product import ProductOut
from app.services.image_intelligence_service import Analysis, AnalysisStatus
from app.services.product_service import SaveResult

# A base64 photo. About 4/3 of the file size, so this allows the default limit with room to spare.
Base64Image = Annotated[str, StringConstraints(min_length=16, max_length=21_000_000)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class AnalyzeIn(BaseModel):
    """A photo to read. Nothing is saved. The picture goes to an image provider ONLY if `use_provider`
    is true, and outside product information is asked for ONLY if `enrich` is true (only the barcode
    is sent for that)."""

    model_config = ConfigDict(extra="forbid")

    image_base64: Base64Image
    content_type: str | None = Field(default=None, max_length=50)
    barcode_hint: Barcode | None = None  # a barcode the browser read from the photo
    use_provider: bool = False
    enrich: bool = False


class SuggestionOut(BaseModel):
    field: str
    value: str
    label: str  # "Detected" or "Suggested": never "confirmed"
    source: str
    category_id: int | None
    unit_id: int | None


class DuplicateOut(BaseModel):
    product_id: int
    name: str
    sku: str
    barcode: str | None
    brand: str | None
    strength: str  # EXACT, LIKELY or POSSIBLE
    reasons: list[str]


class ImageInfoOut(BaseModel):
    content_type: str
    width: int
    height: int
    size_bytes: int


class AnalysisOut(BaseModel):
    """What the photo suggests. The photo itself is not stored and nothing has been created or changed."""

    status: AnalysisStatus
    message: str | None  # "Image analysis is not configured yet." when that is the case
    provider_label: str | None
    sent_to_provider: bool
    image: ImageInfoOut
    barcode: str | None
    barcode_note: str | None
    suggestions: list[SuggestionOut]
    existing_products: list[ProductOut]  # the shop's own product(s) with this barcode
    possible_duplicates: list[DuplicateOut]  # shown as "Possible existing product found"
    visible_text: list[str]
    notes: list[str]
    stored: bool = False  # the photo is not kept by an analysis
    changes_data: bool = False  # an analysis never creates or changes anything

    @classmethod
    def from_analysis(cls, a: Analysis) -> "AnalysisOut":
        from app.services.image_intelligence_service import NOT_CONFIGURED

        return cls(
            status=a.status,
            message=NOT_CONFIGURED if a.status is AnalysisStatus.NOT_CONFIGURED else None,
            provider_label=a.provider_label,
            sent_to_provider=a.sent_to_provider,
            image=ImageInfoOut(
                content_type=a.image.content_type,
                width=a.image.width,
                height=a.image.height,
                size_bytes=a.image.size_bytes,
            ),
            barcode=a.barcode,
            barcode_note=a.barcode_note,
            suggestions=[
                SuggestionOut(
                    field=s.field,
                    value=s.value,
                    label=s.label,
                    source=s.source,
                    category_id=s.category_id,
                    unit_id=s.unit_id,
                )
                for s in a.suggestions
            ],
            existing_products=[ProductOut.from_view(v) for v in a.existing],
            possible_duplicates=[
                DuplicateOut(
                    product_id=m.view.product.id,
                    name=m.view.product.name,
                    sku=m.view.product.sku,
                    barcode=m.view.product.barcode,
                    brand=m.view.product.brand,
                    strength=m.strength.label,
                    reasons=m.reasons,
                )
                for m in a.possible_duplicates
            ],
            visible_text=a.visible_text,
            notes=a.notes,
        )


class ReviewedProduct(BaseModel):
    """The product the user reviewed and edited. No opening stock here: a photo can never change stock."""

    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
    name: Text
    brand: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    category_id: int
    unit_id: int
    default_supplier_id: int | None = None
    reorder_level: QuantityIn = Decimal("0")
    mrp: MoneyIn | None = None
    selling_price: MoneyIn
    purchase_price: MoneyIn | None = None
    barcode: Barcode | None = None


class ConfirmProductIn(BaseModel):
    """The user's confirmation. Creating from a photo always needs this explicit step."""

    model_config = ConfigDict(extra="forbid")

    product: ReviewedProduct
    acknowledge_duplicates: bool = False  # true only after the user saw "Possible existing product found"
    keep_image: bool = False  # true only if the user chose to keep the photo with the product
    image_base64: Base64Image | None = None
    content_type: str | None = Field(default=None, max_length=50)


class AttachImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_base64: Base64Image
    content_type: str | None = Field(default=None, max_length=50)


class ConfirmedOut(BaseModel):
    product: ProductOut
    warnings: list[str]
    image_kept: bool

    @classmethod
    def from_result(cls, result: SaveResult, *, image_kept: bool) -> "ConfirmedOut":
        return cls(product=ProductOut.from_view(result.view), warnings=result.warnings, image_kept=image_kept)


class ProviderStatusOut(BaseModel):
    """Whether photo analysis is available on this server. Never a key, never a provider setting."""

    allowed_by_plan: bool | None
    configured: bool
    provider_label: str | None
    max_bytes: int
    max_side: int
    formats: list[str]


class KeptImageOut(BaseModel):
    product_id: int
    content_type: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
