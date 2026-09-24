"""Public webhook endpoint. Intentionally unauthenticated: a request is believed only after its signature is verified with the shop's
secret (see `webhook_service`). The address carries an unguessable key that identifies the integration; a wrong key and a bad signature both
get a bare refusal that says nothing about which shop or provider exists."""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.deps import client_address
from app.core import ratelimit
from app.db.session import write_transaction
from app.services import webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{webhook_key}")
async def receive(webhook_key: str, request: Request) -> JSONResponse:
    ratelimit.enforce("webhook", client_address(request))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > webhook_service.MAX_BODY_BYTES:
        return JSONResponse({"status": "too_large"}, status_code=413)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items() if k.lower().startswith("x-")}

    def work() -> tuple[int, dict]:
        with write_transaction() as session:
            return webhook_service.receive(session, webhook_key, headers, body)

    status, content = await run_in_threadpool(work)
    return JSONResponse(content, status_code=status)
