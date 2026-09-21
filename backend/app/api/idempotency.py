"""Safe repeats for operations that create or post something: one idempotency mechanism for every router.

A screen sends an `Idempotency-Key` header (a random value it makes once per attempt and keeps for retries
of that same attempt). The first request does its work and remembers the answer under the key; a repeat of
the SAME request (a double tap, a lost response, a retry after a network error) returns that remembered
answer instead of doing the work twice. So a sale is never posted twice and a return never refunds twice
just because a request was sent again. Without a key the operation behaves as before.

The key row is written in the SAME database transaction as the operation. If the operation fails, nothing
is kept, key included, so the retry runs it afresh; if it succeeds, the key and the result are committed
together. The same key with a different request is refused, and so is a key that another request is still
using. Keys are per shop.

This is the mechanism a client must rely on before repeating money or stock operations automatically. Reads
are repeated freely; anything that moves stock, money or a khata balance is only repeated with a key.
"""

import hashlib
import json
import re
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.session import write_transaction
from app.services import idempotency_service
from app.services.errors import InvalidInputError

T = TypeVar("T", bound=BaseModel)
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")
REPLAY_HEADER = "Idempotent-Replay"

IdempotencyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="A random value per attempt; repeats of it are safe."),
]


def _fingerprint(operation: str, payload: Any) -> str:
    text = json.dumps({"operation": operation, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def run_idempotent(
    ctx: RequestContext,
    key: str | None,
    operation: str,
    payload: Any,
    produce: Callable[[Session], T],
    *,
    status_code: int = 200,
) -> T | JSONResponse:
    """Run `produce` in a write transaction, once per key; a repeat returns the remembered result."""
    if key is None:
        with write_transaction() as session:
            return produce(session)
    if not KEY_PATTERN.match(key):
        raise InvalidInputError(
            "The request key is not valid.", field="Idempotency-Key", code="idempotency_key_invalid"
        )
    fingerprint = _fingerprint(operation, payload)
    with write_transaction() as session:
        stored = idempotency_service.find(
            session, ctx.shop_id, key, operation=operation, fingerprint=fingerprint
        )
        if stored is not None:
            return JSONResponse(
                status_code=stored.response_status or status_code,
                content=stored.response_json,
                headers={REPLAY_HEADER: "true"},
            )
        row = idempotency_service.begin(
            session, ctx.shop_id, key, operation=operation, fingerprint=fingerprint
        )
        result = produce(session)
        idempotency_service.complete(row, status=status_code, body=jsonable_encoder(result))
        return result
