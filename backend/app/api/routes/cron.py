"""One pass of the background jobs, for hosts that have no long-running worker (Vercel Cron and similar).

GET /api/cron/tick with `Authorization: Bearer <CRON_SECRET>`. The secret is read from the environment variable named CRON_SECRET (Vercel sends
exactly this header to a cron path when that variable exists). Without the variable the endpoint does not exist (404), and a wrong or missing token is
refused with 401 and no detail. The pass is the same as `python -m app.worker --once --schedule`: queue the routine jobs (idempotent), run what is due.
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from app.core import ratelimit
from app.db.session import write_transaction
from app.services import background_job_service as jobs

router = APIRouter(tags=["cron"])


@router.get("/api/cron/tick", include_in_schema=False)
def tick(authorization: str | None = Header(default=None)) -> dict[str, object]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        raise HTTPException(status_code=404)
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        ratelimit.enforce("admin_auth", "cron")  # a guesser is slowed down
        raise HTTPException(status_code=401)
    with write_transaction() as session:
        jobs.schedule_periodic(session)
    outcomes = jobs.run_due(limit=20)
    return {"ran": len(outcomes), "failed": sum(1 for o in outcomes if o["status"] == "FAILED")}
