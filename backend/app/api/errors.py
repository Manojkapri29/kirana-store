"""Turns service errors into HTTP responses.

Field-level problems use the same shape as FastAPI's own validation errors
(`{"detail": [{"loc": ["body", "sku"], "msg": "...", "type": "..."}]}`), so the frontend has one way to
show them next to the right input. A missing thing is a plain `{"detail": "message"}` with status 404.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.errors import ConflictError, DomainError, EntitlementError, InvalidInputError, NotFoundError


def _location(field: str | None) -> list[str | int]:
    """`"items.2.quantity"` becomes ["body", "items", 2, "quantity"], like FastAPI's own errors."""
    if not field:
        return ["body"]
    return ["body", *(int(part) if part.isdigit() else part for part in field.split("."))]


def _field_errors(exc: DomainError, kind: str) -> list[dict[str, object]]:
    pairs = getattr(exc, "errors", None) or [(exc.field, exc.message)]
    return [{"loc": _location(field), "msg": message, "type": kind} for field, message in pairs]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": _field_errors(exc, "conflict")})

    @app.exception_handler(EntitlementError)
    async def not_in_plan(_: Request, exc: EntitlementError) -> JSONResponse:
        detail = [{"loc": ["body"], "msg": exc.message, "type": "plan_limit", "feature": exc.feature}]
        return JSONResponse(status_code=403, content={"detail": detail})

    @app.exception_handler(InvalidInputError)
    async def invalid(_: Request, exc: InvalidInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _field_errors(exc, "business_rule")})
