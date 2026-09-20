"""Operational endpoints that sit outside the versioned business API."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness check: returns 200 when the API process is up.

    It intentionally does not touch the database. A separate readiness check
    can be added once the database exists (Phase 2).
    """
    return HealthResponse(status="ok")
