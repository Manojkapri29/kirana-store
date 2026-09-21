"""The remembered outcome of a create or post request, so a repeat of it does not do the work twice.

Used through `app.api.idempotency`. All of it runs inside the caller's transaction: the key row and the
operation it guards are committed together or not at all. Keys are per shop.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey
from app.services.errors import ConflictError


def find(
    session: Session, shop_id: int, key: str, *, operation: str, fingerprint: str
) -> IdempotencyKey | None:
    """The stored outcome for this key, if there is one. Refuses a key that belongs to a different request,
    or to
    a request that has not finished."""
    existing = session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.shop_id == shop_id, IdempotencyKey.key == key)
    )
    if existing is None:
        return None
    if existing.request_hash != fingerprint or existing.operation != operation:
        raise ConflictError(
            "This request key was already used for a different request.", code="idempotency_key_reused"
        )
    if existing.response_json is None:
        raise ConflictError("This request is still being processed.", code="request_in_progress")
    return existing


def begin(session: Session, shop_id: int, key: str, *, operation: str, fingerprint: str) -> IdempotencyKey:
    """Claim the key for this request. If another request claimed it a moment earlier, this one is refused."""
    row = IdempotencyKey(shop_id=shop_id, key=key, operation=operation, request_hash=fingerprint)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError("This request is still being processed.", code="request_in_progress") from exc
    return row


def complete(row: IdempotencyKey, *, status: int, body: Any) -> None:
    row.response_status = status
    row.response_json = body
