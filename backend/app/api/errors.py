"""Turns service errors into HTTP responses.

Field-level problems use the same shape as FastAPI's own validation errors
(`{"detail": [{"loc": ["body", "sku"], "msg": "...", "type": "..."}]}`), so the frontend has one way to
show them next to the right input. A missing thing is a plain `{"detail": "message"}` with status 404.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.errors import ConflictError, DomainError, InvalidInputError, NotFoundError


def _field_error(exc: DomainError, kind: str) -> list[dict[str, object]]:
    location = ["body", exc.field] if exc.field else ["body"]
    return [{"loc": location, "msg": exc.message, "type": kind}]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": _field_error(exc, "conflict")})

    @app.exception_handler(InvalidInputError)
    async def invalid(_: Request, exc: InvalidInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _field_error(exc, "business_rule")})
