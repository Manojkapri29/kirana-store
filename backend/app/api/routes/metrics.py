"""`GET /metrics` in the Prometheus text format. Off unless KIRANA_METRICS_ENABLED; then it needs the metrics token."""

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core import metrics, ratelimit
from app.core.config import get_settings

router = APIRouter(include_in_schema=False)


@router.get("/metrics")
def scrape(request: Request) -> PlainTextResponse:
    settings = get_settings()
    if not settings.metrics_enabled or settings.metrics_token is None:
        raise HTTPException(status_code=404, detail="Not found")  # not advertised when it is off
    host = request.client.host if request.client else "unknown"
    ratelimit.enforce("admin_auth", f"metrics:{host}", settings)
    header = request.headers.get("authorization", "")
    presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(presented.encode(), settings.metrics_token.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="A valid metrics token is required.")
    return PlainTextResponse(metrics.registry.render(), media_type="text/plain; version=0.0.4")
