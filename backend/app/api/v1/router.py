"""Aggregates all versioned business routers under `/api/v1`.

Each future module (products, purchases, sales, ...) adds its own router module
and is registered here with `api_v1_router.include_router(...)`.
Routers stay thin: they parse requests and delegate to `app.services`.
"""

from fastapi import APIRouter

api_v1_router = APIRouter()
