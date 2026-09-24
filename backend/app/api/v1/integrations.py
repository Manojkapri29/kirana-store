"""Integration settings, monitoring, customer messages, accounting exports and the location helpers.

Every route needs the integration permission of what it does; changing a payment provider also needs `PAYMENT_INTEGRATION_MANAGE` and
changing a messaging provider `NOTIFICATION_INTEGRATION_MANAGE`. Nothing returned here contains a secret: only the NAME of an environment
variable and whether it is present.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import Ctx, rate_limited
from app.api.idempotency import KEY_PATTERN
from app.db.session import get_session, write_transaction
from app.integrations import location
from app.models.enums import IntegrationType, MessageStatus
from app.schemas.integrations import (
    ConfigureIn,
    DistanceIn,
    GeocodeIn,
    IntegrationEventOut,
    MappingClearIn,
    MappingIn,
    MappingOut,
    MessageIn,
    MessageOut,
    RotateIn,
    WebhookEventOut,
)
from app.services import (
    accounting_export_service,
    authorization_service,
    integration_service,
    messaging_service,
    webhook_service,
)
from app.services.errors import ConflictError, InvalidInputError
from app.services.export_service import ReportFormat

router = APIRouter(prefix="/integrations", tags=["integrations"])
ReadSession = Annotated[Session, Depends(get_session)]
TYPE_PERMISSION = {
    IntegrationType.PAYMENT: "PAYMENT_INTEGRATION_MANAGE",
    IntegrationType.EMAIL: "NOTIFICATION_INTEGRATION_MANAGE",
    IntegrationType.SMS: "NOTIFICATION_INTEGRATION_MANAGE",
    IntegrationType.WHATSAPP: "NOTIFICATION_INTEGRATION_MANAGE",
    IntegrationType.PUSH: "NOTIFICATION_INTEGRATION_MANAGE",
}
SHOP_TYPES = (
    IntegrationType.PAYMENT,
    IntegrationType.EMAIL,
    IntegrationType.SMS,
    IntegrationType.WHATSAPP,
    IntegrationType.PUSH,
    IntegrationType.ACCOUNTING,
)


def _allowed(ctx, itype: IntegrationType) -> None:  # noqa: ANN001
    """Beyond the route's permission, the kind of integration has its own: a person who may manage email may not switch the payment provider."""
    if itype not in SHOP_TYPES:
        raise InvalidInputError(
            "This integration is set for the whole installation in the environment, not per shop.",
            field="integration_type",
        )
    if itype in TYPE_PERMISSION:
        authorization_service.require(ctx, TYPE_PERMISSION[itype])


@router.get("")
def list_integrations(ctx: Ctx, session: ReadSession) -> dict:
    return {
        "items": integration_service.list_integrations(session, ctx.shop_id),
        "platform": integration_service.platform_entries(),
    }


@router.get("/dashboard")
def dashboard(ctx: Ctx, session: ReadSession) -> dict:
    return integration_service.dashboard(session, ctx.shop_id)


@router.get("/logs")
def logs(ctx: Ctx, session: ReadSession, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict:
    return {
        "items": [
            IntegrationEventOut.of(e) for e in integration_service.events(session, ctx.shop_id, limit=limit)
        ]
    }


@router.get("/webhook-events")
def webhook_events(ctx: Ctx, session: ReadSession, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict:
    return {
        "items": [
            WebhookEventOut.of(e) for e in webhook_service.list_events(session, ctx.shop_id, limit=limit)
        ]
    }


@router.get("/messages")
def list_messages(
    ctx: Ctx,
    session: ReadSession,
    status: MessageStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    return {
        "items": [
            MessageOut.of(m)
            for m in messaging_service.list_messages(session, ctx.shop_id, status=status, limit=limit)
        ]
    }


@router.post("/messages", response_model=MessageOut, status_code=201, dependencies=[Depends(rate_limited("message"))])
def send_message(
    payload: MessageIn,
    ctx: Ctx,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MessageOut:
    if idempotency_key is not None and not KEY_PATTERN.match(idempotency_key):
        raise InvalidInputError(
            "The Idempotency-Key must be 8 to 100 letters, digits or . _ : -", field="Idempotency-Key"
        )
    with write_transaction() as session:
        m = messaging_service.send_to_customer(
            session, ctx, customer_id=payload.customer_id, channel=payload.channel, kind=payload.kind, purpose=payload.purpose,
            body=payload.body, subject=payload.subject, idempotency_key=idempotency_key,
        )  # fmt: skip
        return MessageOut.of(m)


@router.post("/platform/storage/test")
def test_storage(ctx: Ctx) -> dict:
    """Write, read and delete one tiny probe object in this shop's own folder of the configured store."""
    import hashlib
    import secrets

    from app.core.config import get_settings
    from app.integrations.base import ProviderError
    from app.services import image_store

    digest = hashlib.sha256(secrets.token_bytes(16)).hexdigest()
    key = image_store.storage_key(ctx.shop_id, digest, "png")
    try:
        store = image_store.default_store(get_settings())
        store.put(key, b"probe")
        ok = store.get(key) == b"probe"
        store.delete(key)
    except (ProviderError, RuntimeError, OSError) as error:
        return {
            "ok": False,
            "code": getattr(error, "code", "storage_error"),
            "provider": get_settings().storage_provider,
        }
    return {
        "ok": ok,
        "code": None if ok else "read_back_mismatch",
        "provider": get_settings().storage_provider,
    }


@router.post("/location/distance")
def distance(payload: DistanceIn, ctx: Ctx) -> dict:
    return {
        "distance_km": str(location.distance_km(payload.lat1, payload.lon1, payload.lat2, payload.lon2)),
        "basis": "Straight line between the two points, not a road distance.",
    }


@router.post("/location/geocode")
def geocode(payload: GeocodeIn, ctx: Ctx) -> Response:
    raise ConflictError("Provider Not Configured", code="provider_not_configured")


@router.get("/accounting/mappings")
def mappings(ctx: Ctx, session: ReadSession) -> dict:
    return {
        "items": [MappingOut.of(m) for m in accounting_export_service.list_mappings(session, ctx.shop_id)],
        "valid_keys": sorted(accounting_export_service.VALID_KEYS),
    }


@router.put("/accounting/mappings", response_model=MappingOut)
def set_mapping(payload: MappingIn, ctx: Ctx) -> MappingOut:
    with write_transaction() as session:
        return MappingOut.of(
            accounting_export_service.set_mapping(
                session, ctx, payload.source_key, payload.external_code, payload.external_name
            )
        )


@router.post("/accounting/mappings/clear")
def clear_mapping(payload: MappingClearIn, ctx: Ctx) -> dict:
    with write_transaction() as session:
        accounting_export_service.clear_mapping(session, ctx, payload.source_key)
    return {"cleared": payload.source_key}


@router.get("/accounting/export/{kind}")
def accounting_export(
    kind: str,
    ctx: Ctx,
    session: ReadSession,
    date_from: date,
    date_to: date,
    fmt: Annotated[ReportFormat, Query(alias="format")] = ReportFormat.CSV,
) -> Response:
    file = accounting_export_service.render(session, ctx, kind, date_from, date_to, fmt)
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.put("/{integration_type}")
def configure(integration_type: IntegrationType, payload: ConfigureIn, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        row = integration_service.configure(
            session, ctx, integration_type, provider=payload.provider, config=payload.config, credential_ref=payload.credential_ref,
            webhook_credential_ref=payload.webhook_credential_ref, is_enabled=payload.is_enabled,
        )  # fmt: skip
        return integration_service.view(row, integration_type)


@router.post("/{integration_type}/enable")
def enable(integration_type: IntegrationType, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        return integration_service.view(
            integration_service.set_enabled(session, ctx, integration_type, True), integration_type
        )


@router.post("/{integration_type}/disable")
def disable(integration_type: IntegrationType, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        return integration_service.view(
            integration_service.set_enabled(session, ctx, integration_type, False), integration_type
        )


@router.post("/{integration_type}/test")
def test(integration_type: IntegrationType, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        return integration_service.test_connection(session, ctx, integration_type)


@router.post("/{integration_type}/rotate-credentials")
def rotate_credentials(integration_type: IntegrationType, payload: RotateIn, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        row = integration_service.rotate_credentials(
            session,
            ctx,
            integration_type,
            credential_ref=payload.credential_ref,
            webhook_credential_ref=payload.webhook_credential_ref,
        )
        return integration_service.view(row, integration_type)


@router.post("/{integration_type}/rotate-webhook-key")
def rotate_webhook_key(integration_type: IntegrationType, ctx: Ctx) -> dict:
    _allowed(ctx, integration_type)
    with write_transaction() as session:
        return integration_service.view(
            integration_service.rotate_webhook_key(session, ctx, integration_type), integration_type
        )
