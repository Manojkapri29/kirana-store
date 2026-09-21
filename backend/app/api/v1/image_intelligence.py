"""Photo capture and image intelligence: an optional layer that only ever SUGGESTS.

`analyze` reads a photo and changes nothing (the photo is not stored). Only `confirm-product`, which the
user calls after reviewing the suggestions, creates a product, and only with their explicit say-so does a
photo get kept with it. The picture goes to an image provider only when the caller sets `use_provider`, and
only if one is configured. Kept photos are private: they are served only to their own shop, never from a
public address.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.idempotency import IdempotencyHeader, run_idempotent
from app.core.config import get_settings
from app.db.session import get_session, write_transaction
from app.schemas.image_intelligence import (
    AnalysisOut,
    AnalyzeIn,
    AttachImageIn,
    ConfirmedOut,
    ConfirmProductIn,
    KeptImageOut,
    ProviderStatusOut,
)
from app.services import entitlement_service, image_intelligence_service, image_validation

router = APIRouter(prefix="/image-intelligence", tags=["image-intelligence"])
ReadSession = Annotated[Session, Depends(get_session)]
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


@router.get("/status", response_model=ProviderStatusOut)
def status(ctx: Ctx, session: ReadSession) -> ProviderStatusOut:
    """What photo analysis can do on this server and in this plan. No keys or provider settings are shown."""
    info = image_intelligence_service.provider_status()
    allowed = entitlement_service.get_entitlements(session, ctx.shop_id).allows(
        image_intelligence_service.FEATURE
    )
    return ProviderStatusOut(allowed_by_plan=allowed, **info)


@router.post("/analyze", response_model=AnalysisOut)
def analyze(payload: AnalyzeIn, ctx: Ctx, session: ReadSession) -> AnalysisOut:
    """Read a photo and suggest what it shows. Nothing is created, changed or kept."""
    settings = get_settings()
    data = image_validation.decode_base64(payload.image_base64, max_bytes=settings.image_max_bytes)
    analysis = image_intelligence_service.analyze(
        session,
        ctx.shop_id,
        data=data,
        declared_type=payload.content_type,
        barcode_hint=payload.barcode_hint,
        use_provider=payload.use_provider,
        enrich=payload.enrich,
        settings=settings,
    )
    return AnalysisOut.from_analysis(analysis)


@router.post("/confirm-product", response_model=ConfirmedOut, status_code=201)
def confirm_product(
    payload: ConfirmProductIn, ctx: Ctx, idempotency_key: IdempotencyHeader = None
) -> ConfirmedOut:
    """Create the product the user reviewed. Refuses with "Possible existing product found" unless the user
    confirmed the duplicates, and keeps the photo only if asked."""
    settings = get_settings()
    image = (
        image_validation.decode_base64(payload.image_base64, max_bytes=settings.image_max_bytes)
        if payload.image_base64
        else None
    )
    fields = payload.product.model_dump()

    def produce(session: Session) -> ConfirmedOut:
        result = image_intelligence_service.confirm_product(
            session,
            ctx,
            fields,
            image=image,
            declared_type=payload.content_type,
            keep_image=payload.keep_image,
            acknowledge_duplicates=payload.acknowledge_duplicates,
            settings=settings,
        )
        return ConfirmedOut.from_result(result, image_kept=payload.keep_image)

    audit_view = payload.model_dump(mode="json", exclude={"image_base64"})
    return run_idempotent(ctx, idempotency_key, "image.confirm_product", audit_view, produce, status_code=201)


@router.put("/products/{product_id}/image", response_model=KeptImageOut)
def keep_image(product_id: int, payload: AttachImageIn, ctx: Ctx) -> KeptImageOut:
    """Keep a photo with an existing product. Changes nothing else about the product."""
    settings = get_settings()
    data = image_validation.decode_base64(payload.image_base64, max_bytes=settings.image_max_bytes)
    with write_transaction() as session:
        row = image_intelligence_service.attach_image(
            session, ctx, product_id, data, declared_type=payload.content_type, settings=settings
        )
        return KeptImageOut(
            product_id=row.product_id,
            content_type=row.content_type,
            width=row.width,
            height=row.height,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
        )


@router.get("/products/{product_id}/image")
def get_image(product_id: int, ctx: Ctx, session: ReadSession) -> Response:
    """The kept photo of a product, to its own shop only."""
    data, content_type = image_intelligence_service.get_image(session, ctx.shop_id, product_id)
    return Response(content=data, media_type=content_type, headers=PRIVATE_HEADERS)


@router.post("/products/{product_id}/image/remove", status_code=204)
def remove_image(product_id: int, ctx: Ctx) -> Response:
    """Stop keeping a product's photo. (An explicit action rather than a DELETE: the API has none.)"""
    with write_transaction() as session:
        image_intelligence_service.delete_image(session, ctx, product_id)
    return Response(status_code=204)
