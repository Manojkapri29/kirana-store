"""Integrations: which external service a shop uses for what, whether it is really usable, and a payload-free record of how calls go.

    business code -> a service (payments, messaging, exports) -> `provider_for(...)` -> an adapter in `app/integrations` -> the outside world

Nothing here names a vendor. A shop chooses a PROVIDER from a fixed catalogue for each integration TYPE, gives its non-secret settings, and
names the environment variable that holds its credential. The value is read only when a call is made (`base.resolve_secret`) and never
appears in a response, a log line, a row or an error. "Configured" is a fact checked from the environment, not a flag someone set:
without the credential the state is NOT_CONFIGURED and the answer to the user is "Provider Not Configured".

Calls go through `call`, which records the outcome (no payload) and applies the retry rule: only SAFE operations (reads, status checks) are
repeated automatically, and only when the failure said a retry may help; anything that moves money or sends a message is tried once.
"""

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.integrations import base, http_json, location, payments, smtp_email
from app.integrations.base import SAFE, ProviderError
from app.models import Integration, IntegrationEvent
from app.models.enums import IntegrationStatus, IntegrationType
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

T = TypeVar("T")
ERROR_AFTER_FAILURES = 3
MAX_SAFE_ATTEMPTS = 3
_SECRETISH = re.compile(r"(pass|secret|token|key|credential|auth)", re.I)
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
IT = IntegrationType


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    fields: dict[str, str] = field(
        default_factory=dict
    )  # allowed non-secret setting -> "str" | "int" | "choice:a,b" | "url" | "host"
    required: tuple[str, ...] = ()
    needs_secret: bool = False
    secret_when: str | None = (
        None  # a setting whose presence means the credential is needed too (an SMTP user name needs its password)
    )
    external: bool = True  # False: no outside system is involved
    webhook: bool = False  # receives signed webhooks (needs a webhook key and secret)
    note: str = ""


CATALOG: dict[IntegrationType, dict[str, ProviderSpec]] = {
    IT.PAYMENT: {
        "manual": ProviderSpec(
            "Manual confirmation (cash on delivery, UPI shown on a phone)",
            external=False,
            note="A person attests the money arrived; nothing outside can verify it.",
        ),
        "generic_webhook": ProviderSpec(
            "Signed webhook from any gateway or relay",
            webhook=True,
            note="Payments are created at the gateway and linked here; states arrive only by verified webhook.",
        ),
    },
    IT.EMAIL: {
        "smtp": ProviderSpec(
            "SMTP mail server",
            {
                "host": "host",
                "port": "int",
                "security": "choice:starttls,ssl,none",
                "sender": "str",
                "username": "str",
            },
            ("host", "port", "security", "sender"),
            secret_when="username",
        ),  # the password is optional (an open relay on a private network needs none) unless a user name is given
    },
    IT.SMS: {
        "http_json": ProviderSpec(
            "HTTPS JSON gateway", {"url": "url", "sender": "str"}, ("url",), needs_secret=True
        )
    },
    IT.WHATSAPP: {
        "http_json": ProviderSpec(
            "HTTPS JSON gateway", {"url": "url", "sender": "str"}, ("url",), needs_secret=True
        )
    },
    IT.PUSH: {
        "http_json": ProviderSpec(
            "HTTPS JSON gateway", {"url": "url", "sender": "str"}, ("url",), needs_secret=True
        )
    },
    IT.MAPS: {},  # no location provider is bundled
    IT.PRODUCT_DATA: {},  # UPCitemdb is configured platform-wide in the environment (see `platform_entries`)
    IT.ACCOUNTING: {
        "generic_export": ProviderSpec(
            "Generic file export (CSV / Excel)",
            external=False,
            note="Builds files from the finance ledger; no outside system is connected.",
        )
    },
}
MESSAGE_TYPES = (IT.EMAIL, IT.SMS, IT.WHATSAPP, IT.PUSH)


# --- Reading ------------------------------------------------------------------------------------------------------


def _row(session: Session, shop_id: int, itype: IntegrationType) -> Integration | None:
    return session.scalar(
        select(Integration).where(Integration.shop_id == shop_id, Integration.integration_type == itype)
    )


def get_row(session: Session, shop_id: int, itype: IntegrationType) -> Integration:
    row = _row(session, shop_id, itype)
    if row is None:
        raise NotFoundError("This integration has not been set up.")
    return row


def _spec(row: Integration) -> ProviderSpec | None:
    return CATALOG.get(row.integration_type, {}).get(row.provider)


def compute_status(row: Integration) -> IntegrationStatus:
    spec = _spec(row)
    if not row.is_enabled:
        return IntegrationStatus.DISABLED
    if spec is None:
        return IntegrationStatus.NOT_CONFIGURED
    if _needs_secret(row, spec) and not base.has_secret(row.credential_ref):
        return IntegrationStatus.NOT_CONFIGURED
    if any(not row.config.get(name) for name in spec.required):
        return IntegrationStatus.NOT_CONFIGURED
    if spec.webhook and not (row.webhook_key and base.has_secret(row.webhook_credential_ref)):
        return IntegrationStatus.NOT_CONFIGURED
    if row.failure_count >= ERROR_AFTER_FAILURES:
        return IntegrationStatus.ERROR
    return IntegrationStatus.CONFIGURED


def _needs_secret(row: Integration, spec: ProviderSpec) -> bool:
    return spec.needs_secret or bool(spec.secret_when and row.config.get(spec.secret_when))


def _refresh(row: Integration) -> None:
    row.status = compute_status(row)


def configuration_message(row: Integration | None) -> str:
    """The plain words for the user: 'Provider Not Configured', or what is missing."""
    if row is None or _spec(row) is None:
        return "Provider Not Configured"
    spec = _spec(row)
    assert spec is not None
    if not row.is_enabled:
        return "Disabled"
    if _needs_secret(row, spec) and not base.has_secret(row.credential_ref):
        return "Credentials Not Configured"
    if any(not row.config.get(name) for name in spec.required):
        return "Provider Not Configured"
    if spec.webhook and not (row.webhook_key and base.has_secret(row.webhook_credential_ref)):
        return "Webhook Secret Not Configured"
    return "Configured"


def view(row: Integration | None, itype: IntegrationType) -> dict[str, Any]:
    """A safe description of one integration: no secret, no environment variable's value, never more than a yes or no."""
    spec = _spec(row) if row else None
    return {
        "integration_type": itype.value,
        "provider": row.provider if row else None,
        "provider_label": spec.label if spec else None,
        "available_providers": [
            {
                "provider": k,
                "label": v.label,
                "external": v.external,
                "webhook": v.webhook,
                "needs_credentials": v.needs_secret,
                "settings": list(v.fields),
                "required": list(v.required),
                "note": v.note,
            }
            for k, v in CATALOG.get(itype, {}).items()
        ],
        "is_enabled": bool(row and row.is_enabled),
        "status": (row.status if row else IntegrationStatus.NOT_CONFIGURED).value,
        "message": configuration_message(row),
        "config": dict(row.config) if row else {},
        "credential_ref": row.credential_ref if row else None,
        "credentials_present": base.has_secret(row.credential_ref) if row else False,
        "webhook_path": f"/api/v1/webhooks/{row.webhook_key}"
        if row and row.webhook_key and spec and spec.webhook
        else None,
        "webhook_secret_present": base.has_secret(row.webhook_credential_ref) if row else False,
        "webhook_credential_ref": row.webhook_credential_ref if row else None,
        "last_success_at": row.last_success_at if row else None,
        "last_failure_at": row.last_failure_at if row else None,
        "failure_count": row.failure_count if row else 0,
        "last_error_code": row.last_error_code if row else None,
        "rotated_at": row.rotated_at if row else None,
        "external": spec.external if spec else True,
    }


def platform_entries() -> list[dict[str, Any]]:
    """Integrations that are configured for the whole installation, not per shop: read-only here, set in the environment."""
    settings = get_settings()
    storage_ok = settings.storage_provider == "local" or bool(
        settings.storage_s3_endpoint
        and settings.storage_s3_bucket
        and base.has_secret(settings.storage_credentials_ref)
    )
    barcode = settings.upcitemdb_api_key is not None
    return [
        {
            "integration_type": IT.STORAGE.value,
            "provider": settings.storage_provider,
            "status": "CONFIGURED" if storage_ok else "NOT_CONFIGURED",
            "message": "Configured" if storage_ok else "Credentials Not Configured",
            "external": settings.storage_provider != "local",
            "note": "Set with KIRANA_STORAGE_* variables; S3 is not verified against a live provider.",
        },
        {
            "integration_type": IT.PRODUCT_DATA.value,
            "provider": "upcitemdb",
            "status": "CONFIGURED" if barcode else "NOT_CONFIGURED",
            "message": "Configured" if barcode else "Provider Not Configured",
            "external": True,
            "note": "Suggestions only: an outside product record never changes your product until you confirm it.",
        },
        {
            "integration_type": IT.MAPS.value,
            "provider": None,
            "status": "NOT_CONFIGURED",
            "message": "Provider Not Configured",
            "external": True,
            "note": "Distance between two known points works without a provider; address lookup needs one.",
        },
    ]


def list_integrations(session: Session, shop_id: int) -> list[dict[str, Any]]:
    rows = {
        r.integration_type: r
        for r in session.scalars(select(Integration).where(Integration.shop_id == shop_id))
    }
    per_shop = (IT.PAYMENT, IT.EMAIL, IT.SMS, IT.WHATSAPP, IT.PUSH, IT.ACCOUNTING)
    out = []
    for itype in per_shop:
        row = rows.get(itype)
        if row is not None:
            _refresh(row)
        out.append(view(row, itype))
    return out


def dashboard(session: Session, shop_id: int) -> dict[str, Any]:
    items = list_integrations(session, shop_id)
    return {
        "integrations": items,
        "platform": platform_entries(),
        "summary": {
            "configured": sum(1 for i in items if i["status"] == "CONFIGURED"),
            "errors": sum(1 for i in items if i["status"] == "ERROR"),
            "not_configured": sum(1 for i in items if i["status"] == "NOT_CONFIGURED"),
            "disabled": sum(1 for i in items if i["status"] == "DISABLED"),
        },
    }


# --- Configuring --------------------------------------------------------------------------------------------------


def _clean_config(spec: ProviderSpec, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise InvalidInputError("Settings must be an object.", field="config")
    out: dict[str, Any] = {}
    for name, value in config.items():
        if name not in spec.fields:
            if _SECRETISH.search(str(name)):
                raise InvalidInputError(
                    "Secrets are never stored here: put them in an environment variable and give its name.",
                    field="config",
                )
            raise InvalidInputError(f"'{name}' is not a setting of this provider.", field="config")
        kind = spec.fields[name]
        if value is None or value == "":
            continue
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise InvalidInputError(f"'{name}' must be a whole number from 1 to 65535.", field="config")
        elif kind.startswith("choice:"):
            if value not in kind.removeprefix("choice:").split(","):
                raise InvalidInputError(
                    f"'{name}' must be one of {kind.removeprefix('choice:')}.", field="config"
                )
        else:
            if not isinstance(value, str) or len(value) > 300 or any(ord(c) < 32 for c in value):
                raise InvalidInputError(f"'{name}' is not a valid value.", field="config")
            if kind == "host" and not _HOST.match(value):
                raise InvalidInputError("Give a host name only, without http:// or a path.", field="config")
            if kind == "url":
                try:
                    base.safe_outbound_url(value, resolve=False)
                except ProviderError as exc:
                    raise InvalidInputError(
                        "The address must be an https URL without a user name or password.", field="config"
                    ) from exc
        out[name] = value
    return out


def configure(
    session: Session, ctx: RequestContext, itype: IntegrationType, *, provider: str, config: dict[str, Any],
    credential_ref: str | None = None, webhook_credential_ref: str | None = None, is_enabled: bool | None = None,
) -> Integration:  # fmt: skip
    spec = CATALOG.get(itype, {}).get(provider)
    if spec is None:
        raise InvalidInputError(
            f"Choose one of: {', '.join(CATALOG.get(itype, {})) or 'none (no provider exists for this type)'}.",
            field="provider",
        )
    for ref, name in ((credential_ref, "credential_ref"), (webhook_credential_ref, "webhook_credential_ref")):
        if ref and not base.valid_ref(ref):
            raise InvalidInputError(
                f"The name of the environment variable must look like {base.SECRET_PREFIX}NAME (capital letters, digits, underscores).",
                field=name,
            )
    if webhook_credential_ref and not spec.webhook:
        raise InvalidInputError("This provider does not receive webhooks.", field="webhook_credential_ref")
    row = _row(session, ctx.shop_id, itype)
    created = row is None
    if row is None:
        row = Integration(
            shop_id=ctx.shop_id, integration_type=itype, provider=provider, config={}, created_by=ctx.user_id
        )
        session.add(row)
    row.provider = provider
    row.config = _clean_config(spec, config)
    row.credential_ref = credential_ref or None
    row.webhook_credential_ref = webhook_credential_ref or None
    if spec.webhook and not row.webhook_key:
        row.webhook_key = secrets.token_urlsafe(24)
    if is_enabled is not None:
        row.is_enabled = is_enabled
    row.failure_count, row.last_error_code = 0, None
    session.flush()
    _refresh(row)
    record_audit(
        session, ctx, entity_type="integration", entity_id=row.id, action="integration_configured" if created else "integration_updated",
        after={"type": itype.value, "provider": provider, "enabled": row.is_enabled, "credential_ref_set": bool(row.credential_ref)},
    )  # fmt: skip
    return row


def set_enabled(session: Session, ctx: RequestContext, itype: IntegrationType, enabled: bool) -> Integration:
    row = get_row(session, ctx.shop_id, itype)
    row.is_enabled = enabled
    _refresh(row)
    record_audit(
        session,
        ctx,
        entity_type="integration",
        entity_id=row.id,
        action="integration_enabled" if enabled else "integration_disabled",
        after={"type": itype.value},
    )
    return row


def rotate_credentials(
    session: Session,
    ctx: RequestContext,
    itype: IntegrationType,
    *,
    credential_ref: str | None = None,
    webhook_credential_ref: str | None = None,
) -> Integration:
    """Point the integration at a new environment variable (the new value is set by the operator; nothing is copied). The old name is simply no longer used."""
    row = get_row(session, ctx.shop_id, itype)
    for ref, name in ((credential_ref, "credential_ref"), (webhook_credential_ref, "webhook_credential_ref")):
        if ref is not None and not base.valid_ref(ref):
            raise InvalidInputError(
                f"The name of the environment variable must look like {base.SECRET_PREFIX}NAME.", field=name
            )
    if credential_ref is None and webhook_credential_ref is None:
        raise InvalidInputError("Give the new variable name to use.", field="credential_ref")
    if credential_ref is not None:
        row.credential_ref = credential_ref
    if webhook_credential_ref is not None:
        row.webhook_credential_ref = webhook_credential_ref
    row.rotated_at, row.failure_count, row.last_error_code = utc_now(), 0, None
    _refresh(row)
    record_audit(
        session,
        ctx,
        entity_type="integration",
        entity_id=row.id,
        action="integration_credentials_rotated",
        after={"type": itype.value},
    )
    return row


def rotate_webhook_key(session: Session, ctx: RequestContext, itype: IntegrationType) -> Integration:
    """A new public webhook address. The old one stops working at once."""
    row = get_row(session, ctx.shop_id, itype)
    spec = _spec(row)
    if spec is None or not spec.webhook:
        raise ConflictError("This provider does not receive webhooks.")
    row.webhook_key = secrets.token_urlsafe(24)
    row.rotated_at = utc_now()
    record_audit(
        session,
        ctx,
        entity_type="integration",
        entity_id=row.id,
        action="integration_webhook_key_rotated",
        after={"type": itype.value},
    )
    return row


# --- Adapters and calls -------------------------------------------------------------------------------------------


def build_message_provider(row: Integration) -> base.MessageProvider | None:
    """The adapter for a configured, enabled messaging integration, or None."""
    if row.integration_type not in MESSAGE_TYPES or compute_status(row) not in (
        IntegrationStatus.CONFIGURED,
        IntegrationStatus.ERROR,
    ):
        return None
    secret = base.resolve_secret(row.credential_ref)
    if row.provider == "smtp":
        c = row.config
        return smtp_email.SmtpEmailProvider(
            host=c["host"],
            port=int(c["port"]),
            security=c["security"],
            sender=c["sender"],
            username=c.get("username"),
            password=secret,
        )
    if row.provider == "http_json":
        return http_json.build(row.integration_type.value, row.config, secret)
    return None


def message_provider_for(
    session: Session, shop_id: int, channel: IntegrationType
) -> tuple[Integration, base.MessageProvider] | None:
    row = _row(session, shop_id, channel)
    if row is None:
        return None
    provider = build_message_provider(row)
    return (row, provider) if provider else None


def payment_provider_for(session: Session, shop_id: int) -> tuple[Integration, base.PaymentProvider] | None:
    row = _row(session, shop_id, IT.PAYMENT)
    if row is None or compute_status(row) not in (IntegrationStatus.CONFIGURED, IntegrationStatus.ERROR):
        return None
    factory = payments.PROVIDERS.get(row.provider)
    return (row, factory()) if factory else None


def note_result(
    session: Session, row: Integration | None, *, itype: IntegrationType, provider: str, operation: str, ok: bool,
    error_code: str | None = None, started: float | None = None,
) -> None:  # fmt: skip
    """Record one outcome: the call log (no payload) and the integration's success/failure counters."""
    if row is None:
        return
    now = utc_now()
    session.add(
        IntegrationEvent(
            shop_id=row.shop_id, integration_type=itype, provider=provider[:40], operation=operation[:60],
            outcome="SUCCESS" if ok else "FAILURE", error_code=(error_code or "")[:60] or None,
            duration_ms=int((time.monotonic() - started) * 1000) if started else 0,
        )
    )  # fmt: skip
    if ok:
        row.last_success_at, row.failure_count, row.last_error_code = now, 0, None
    else:
        row.last_failure_at, row.failure_count, row.last_error_code = (
            now,
            row.failure_count + 1,
            (error_code or "error")[:60],
        )
    _refresh(row)


def call(
    session: Session,
    row: Integration,
    provider_name: str,
    operation: str,
    fn: Callable[[], T],
    *,
    kind: str,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run one external call, record it, and apply the retry rule. `kind` is SAFE (reads, status checks: retried on a retryable failure, up to
    three tries) or UNSAFE (tried once). Raises `ProviderError` after recording the failure."""
    attempts = MAX_SAFE_ATTEMPTS if kind == SAFE else 1
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            result = fn()
        except ProviderError as error:
            note_result(
                session,
                row,
                itype=row.integration_type,
                provider=provider_name,
                operation=operation,
                ok=False,
                error_code=error.code,
                started=started,
            )
            if attempt < attempts and error.retryable:
                sleep(min(0.25 * 2 ** (attempt - 1), 2.0))
                continue
            raise
        except Exception as exc:  # noqa: BLE001  (an adapter bug is a failed call, never a crash)
            note_result(
                session,
                row,
                itype=row.integration_type,
                provider=provider_name,
                operation=operation,
                ok=False,
                error_code="provider_error",
                started=started,
            )
            raise ProviderError("provider_error", retryable=False) from exc
        note_result(
            session,
            row,
            itype=row.integration_type,
            provider=provider_name,
            operation=operation,
            ok=True,
            started=started,
        )
        return result
    raise AssertionError("unreachable")  # pragma: no cover


def test_connection(session: Session, ctx: RequestContext, itype: IntegrationType) -> dict[str, Any]:
    """Check the connection and credentials WITHOUT sending a message or moving money. Returns the outcome; a failure is recorded, not raised."""
    row = get_row(session, ctx.shop_id, itype)
    _refresh(row)
    message = configuration_message(row)
    if row.status not in (IntegrationStatus.CONFIGURED, IntegrationStatus.ERROR):
        return {"ok": False, "code": "not_configured", "message": message}
    try:
        if itype in MESSAGE_TYPES:
            provider = build_message_provider(row)
            if provider is None:
                return {"ok": False, "code": "not_configured", "message": "Provider Not Configured"}
            call(session, row, row.provider, "check", lambda: provider.check(timeout=10.0), kind=SAFE)
        elif itype is IT.PAYMENT:
            spec = _spec(row)
            note_result(session, row, itype=itype, provider=row.provider, operation="check", ok=True)
            return {
                "ok": True,
                "code": None,
                "message": "Configured"
                + ("" if spec and spec.external else " (no outside system: nothing to connect to)"),
            }
        else:
            note_result(session, row, itype=itype, provider=row.provider, operation="check", ok=True)
    except ProviderError as error:
        return {"ok": False, "code": error.code, "message": "The provider did not accept the connection."}
    record_audit(
        session,
        ctx,
        entity_type="integration",
        entity_id=row.id,
        action="integration_tested",
        after={"type": itype.value, "ok": True},
    )
    return {"ok": True, "code": None, "message": "The connection and credentials were accepted."}


def events(
    session: Session, shop_id: int, *, limit: int = 100, before: datetime | None = None
) -> list[IntegrationEvent]:
    query = select(IntegrationEvent).where(IntegrationEvent.shop_id == shop_id)
    if before is not None:
        query = query.where(IntegrationEvent.created_at < before)
    return list(session.scalars(query.order_by(IntegrationEvent.id.desc()).limit(min(limit, 500))))


_ = location  # distance is available without a provider; see app/integrations/location.py
