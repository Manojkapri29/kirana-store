"""FastAPI application entry point.

Run locally with:  uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import health
from app.api.v1.router import api_v1_router
from app.core import diagnostics, observability
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    problems = settings.production_problems()
    if problems:
        # Fail safely: refuse to start rather than run in production with a missing secret or open CORS.
        # The messages name the settings, never their values.
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
    observability.configure_logging(settings.log_level, settings.log_format)
    diagnostics.configure(settings.diagnostics_log_file)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        # Interactive docs are handy in development but not exposed in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],  # no DELETE: nothing is deleted
        allow_headers=["Content-Type", "Accept", "Idempotency-Key", "X-Request-ID", "X-Admin-Token"],
        expose_headers=[
            "Content-Disposition",
            "X-Request-ID",
        ],  # export file names; the request correlation id
    )
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(api_v1_router, prefix="/api/v1")
    return app


app = create_app()
