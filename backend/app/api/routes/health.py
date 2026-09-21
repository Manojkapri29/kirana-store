"""Operational endpoints that sit outside the versioned business API.

  GET /health        liveness (kept for existing monitors): the process is up.
  GET /health/live   liveness: the process is up. Touches nothing else, so it stays green while a dependency is down.
  GET /health/ready  readiness: can this instance safely serve requests? Checks the database connection, that the
                     database is at the migration this code expects, and that the configuration is valid.

Both are public and say nothing about the infrastructure: only "ok" or "unavailable" and the NAMES of the checks that
passed or failed. Detail (revisions, disk, event counts) is available only to authorised administrators at
`/api/v1/admin/system/health`.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core import observability, schema_state
from app.core.config import get_settings
from app.db.session import read_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness (compatibility alias)")
@router.get("/health/live", response_model=HealthResponse, summary="Liveness: is the process alive?")
def live() -> HealthResponse:
    return HealthResponse(status="ok")


def readiness_checks() -> dict[str, str]:
    checks = {"database": "ok", "schema": "ok", "configuration": "ok"}
    try:
        with read_session() as session:
            session.execute(text("SELECT 1"))
            revision = schema_state.database_revision(session.connection())
        if revision != schema_state.code_head():
            checks["schema"] = "unavailable"
    except Exception:  # noqa: BLE001  (any failure means "not ready"; the reason is for the admin view, not here)
        checks["database"] = "unavailable"
        checks["schema"] = "unavailable"
    if get_settings().production_problems():
        checks["configuration"] = "unavailable"
    return checks


@router.get("/health/ready", response_model=ReadyResponse, summary="Readiness: can it serve requests safely?")
def ready(response: Response) -> ReadyResponse:
    checks = readiness_checks()
    ok = all(value == "ok" for value in checks.values())
    if not ok:
        response.status_code = 503
        observability.log_event(
            "application",
            "readiness check failed",
            level=40,
            failed=[k for k, v in checks.items() if v != "ok"],
        )
    return ReadyResponse(status="ok" if ok else "unavailable", checks=checks)
