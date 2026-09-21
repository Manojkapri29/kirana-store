"""Turns every failure into one safe, consistent HTTP response. The only place that does.

Every error body has the same shape:

    {"success": false, "error_code": "insufficient_stock", "message": "...", "category":
    "insufficient_stock",
     "retryable": false, "reference_id": null, "detail": ...}

`detail` is the shape the app already used (a message for a missing thing; a list of
`{"loc": [...], "msg": ..., "type": ...}` for field-level problems), so every screen keeps working and can
show a problem next to the right input. The other fields are new and are what the recovery screens use.

Expected problems (validation, a missing record, a conflict, a plan limit) carry their own message. An
UNEXPECTED failure (a bug, the database, a timeout) never reveals anything: the user gets a plain message
and a `reference_id`; the real exception is written to the internal diagnostics log under that id, with the
endpoint, the shop and user, a category and the request's correlation id (see `app.core.diagnostics`).

`retryable` says whether trying the SAME request again could succeed (a busy database, a timeout, an outside
service). It is never true for a validation, stock or duplicate problem, and it does not by itself make a
repeat safe: money and stock operations are only repeated with an idempotency key (see
`app.api.idempotency`).
"""

import logging
import re
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core import diagnostics, metrics, observability
from app.core.diagnostics import ErrorCategory
from app.services.errors import (
    AccountRestrictedError,
    AiServiceError,
    AuthenticationError,
    ConflictError,
    DomainError,
    EntitlementError,
    FeatureOffError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)

GENERIC_MESSAGE = "Something went wrong while completing this action."
MESSAGES = {
    ErrorCategory.UNEXPECTED: GENERIC_MESSAGE,
    ErrorCategory.CHECKOUT: "We couldn't complete this sale.",
    ErrorCategory.DATABASE: "We couldn't save your changes right now. Please try again in a moment.",
    ErrorCategory.TIMEOUT: "This is taking longer than expected.",
    ErrorCategory.EXTERNAL_API: "An outside service is temporarily unavailable.",
    ErrorCategory.IMAGE_UPLOAD: "The image couldn't be processed.",
    ErrorCategory.PROMOTION: "The offers for this bill couldn't be worked out right now.",
    ErrorCategory.NETWORK: "The connection was interrupted.",
    ErrorCategory.AUTHENTICATION: "Please sign in to continue.",
    ErrorCategory.AUTHORIZATION: "You do not have permission to do this.",
}
RETRYABLE = {ErrorCategory.DATABASE, ErrorCategory.TIMEOUT, ErrorCategory.EXTERNAL_API, ErrorCategory.NETWORK}
_CODES_BY_CATEGORY = {
    ErrorCategory.VALIDATION: "validation_error",
    ErrorCategory.NOT_FOUND: "not_found",
    ErrorCategory.CONFLICT: "conflict",
    ErrorCategory.DUPLICATE: "duplicate_record",
    ErrorCategory.INSUFFICIENT_STOCK: "insufficient_stock",
    ErrorCategory.INVENTORY_CONFLICT: "inventory_conflict",
    ErrorCategory.AUTHENTICATION: "unauthenticated",
    ErrorCategory.AUTHORIZATION: "forbidden",
    ErrorCategory.PLAN_LIMIT: "plan_limit",
    ErrorCategory.DATABASE: "database_error",
    ErrorCategory.TIMEOUT: "timeout",
    ErrorCategory.EXTERNAL_API: "external_service_error",
    ErrorCategory.IMAGE_UPLOAD: "image_error",
    ErrorCategory.PROMOTION: "promotion_error",
    ErrorCategory.CHECKOUT: "checkout_error",
    ErrorCategory.RATE_LIMITED: "rate_limited",
    ErrorCategory.ACCOUNT: "account_restricted",
    ErrorCategory.UNEXPECTED: "internal_error",
}
_CODE_CATEGORIES = {
    "insufficient_stock": ErrorCategory.INSUFFICIENT_STOCK,
    "duplicate_record": ErrorCategory.DUPLICATE,
    "possible_duplicate": ErrorCategory.DUPLICATE,
    "inventory_conflict": ErrorCategory.INVENTORY_CONFLICT,
}
_CONTEXTS: list[tuple[re.Pattern[str], ErrorCategory]] = [
    (re.compile(r"^/api/v1/(sales|quick-sales)/\d+/post$"), ErrorCategory.CHECKOUT),
    (re.compile(r"^/api/v1/(sales|sales-returns|purchase-returns)$"), ErrorCategory.CHECKOUT),
    (re.compile(r"^/api/v1/(sales/calculate|promotions)"), ErrorCategory.PROMOTION),
    (re.compile(r"^/api/v1/image-intelligence"), ErrorCategory.IMAGE_UPLOAD),
    (re.compile(r"^/api/v1/(price-intelligence|products/lookup)"), ErrorCategory.EXTERNAL_API),
]


def _location(field: str | None) -> list[str | int]:
    """`"items.2.quantity"` becomes ["body", "items", 2, "quantity"], like FastAPI's own errors."""
    if not field:
        return ["body"]
    return ["body", *(int(part) if part.isdigit() else part for part in field.split("."))]


def _field_errors(exc: DomainError, kind: str) -> list[dict[str, object]]:
    pairs = getattr(exc, "errors", None) or [(exc.field, exc.message)]
    return [{"loc": _location(field), "msg": message, "type": kind} for field, message in pairs]


def error_body(
    category: ErrorCategory,
    message: str,
    *,
    detail: Any,
    error_code: str | None = None,
    reference_id: str | None = None,
    retryable: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": error_code or _CODES_BY_CATEGORY[category],
        "message": message,
        "category": category.value,
        "retryable": category in RETRYABLE if retryable is None else retryable,
        "reference_id": reference_id,
        "detail": detail,
        **(extra or {}),
    }


def _data(exc: DomainError) -> dict[str, Any] | None:
    return {"data": exc.data} if exc.data else None


def _state(request: Request, name: str) -> Any:
    return getattr(request.state, name, None)


def _endpoint(request: Request) -> str:
    """The request path with record numbers masked (`/sales/{id}/post`): groups errors by endpoint and
    keeps record identifiers out of the diagnostics."""
    return re.sub(r"/\d+(?=/|$)", "/{id}", request.url.path)


def _respond(
    request: Request, status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> JSONResponse:
    correlation = _state(request, "correlation_id")
    merged = {**(headers or {}), **({"X-Request-ID": correlation} if correlation else {})}
    return JSONResponse(status_code=status, content=body, headers=merged, media_type="application/json")


def _log(request: Request, category: ErrorCategory, error_code: str, status: int, reference: str | None,
         exc: BaseException | None) -> None:  # fmt: skip
    diagnostics.record_error(
        reference_id=reference,
        category=category,
        error_code=error_code,
        status=status,
        endpoint=f"{request.method} {_endpoint(request)}",
        method=request.method,
        correlation_id=_state(request, "correlation_id"),
        shop_id=_state(request, "shop_id"),
        user_id=_state(request, "user_id"),
        exc=exc,
    )


def _unexpected_category(request: Request, default: ErrorCategory) -> ErrorCategory:
    if request.method == "GET" and default is ErrorCategory.UNEXPECTED:
        return default
    for pattern, category in _CONTEXTS:
        if pattern.search(request.url.path) and default is ErrorCategory.UNEXPECTED:
            return category
    return default


def _unexpected(
    request: Request,
    exc: BaseException,
    category: ErrorCategory,
    status: int,
    *,
    retryable: bool | None = None,
) -> JSONResponse:
    """A failure the user cannot fix or see the inside of: log everything, reveal only a reference."""
    category = _unexpected_category(request, category)
    if category is ErrorCategory.DATABASE:
        metrics.record_db_error()
    # A caller that already recorded this failure under a reference (an AI action) shows that same reference.
    preset = getattr(exc, "reference_id", None)
    reference = (
        preset
        if isinstance(preset, str) and diagnostics.REFERENCE_PATTERN.match(preset)
        else diagnostics.new_reference_id()
    )
    message = MESSAGES[category]
    _log(request, category, _CODES_BY_CATEGORY[category], status, reference, exc)
    body = error_body(
        category,
        message,
        detail=f"{message} Reference: {reference}",
        reference_id=reference,
        retryable=retryable,
    )
    return _respond(request, status, body)


def _domain_category(exc: DomainError, default: ErrorCategory) -> ErrorCategory:
    code = exc.code or ""
    if code in _CODE_CATEGORIES:
        return _CODE_CATEGORIES[code]
    if code.startswith("image_"):
        return ErrorCategory.IMAGE_UPLOAD
    return default


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Gives every request a correlation id (kept from a well-formed `X-Request-ID`, else generated).

    It returns the id in the response, writes one access-log line (method, masked path, status, duration,
    shop and user ids, never the query string or body), and adds the standard security headers. A problem
    reported by a user can be matched to the server's log line by this id."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        incoming = request.headers.get("x-request-id", "")
        request_id = (
            incoming if diagnostics.CORRELATION_PATTERN.match(incoming) else diagnostics.new_correlation_id()
        )
        request.state.correlation_id = request_id
        observability.set_request_id(request_id)
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = _endpoint(request)
            noisy = path.startswith("/health")
            metrics.record_request(request.method, request.url.path, status, time.perf_counter() - started)
            observability.log_event(
                "access", f"{request.method} {path} {status}",
                level=logging.DEBUG if noisy else logging.INFO,
                request_id=request_id, method=request.method, endpoint=path, status=status,
                duration_ms=duration_ms,
                shop_id=_state(request, "shop_id"), user_id=_state(request, "user_id"),
            )  # fmt: skip
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault(
                "Cache-Control", "no-store"
            )  # business data is never cached by a browser
        return response


def register_error_handlers(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        body = error_body(ErrorCategory.NOT_FOUND, exc.message, detail=exc.message)
        return _respond(request, 404, body)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        category = _domain_category(exc, ErrorCategory.CONFLICT)
        detail = _field_errors(exc, "conflict")
        body = error_body(category, exc.message, detail=detail, error_code=exc.code, extra=_data(exc))
        return _respond(request, 409, body)

    @app.exception_handler(EntitlementError)
    async def not_in_plan(request: Request, exc: EntitlementError) -> JSONResponse:
        detail = [{"loc": ["body"], "msg": exc.message, "type": "plan_limit", "feature": exc.feature}]
        body = error_body(
            ErrorCategory.PLAN_LIMIT, exc.message, detail=detail, extra={"feature": exc.feature}
        )
        return _respond(request, 403, body)

    @app.exception_handler(RateLimitedError)
    async def rate_limited(request: Request, exc: RateLimitedError) -> JSONResponse:
        observability.log_event(
            "security", "rate limit reached", level=logging.WARNING, group=exc.group,
            endpoint=_endpoint(request), method=request.method, shop_id=_state(request, "shop_id"),
        )  # fmt: skip
        body = error_body(ErrorCategory.RATE_LIMITED, exc.message, detail=exc.message, retryable=True,
                          extra={"retry_after": exc.retry_after})  # fmt: skip
        return _respond(request, 429, body, {"Retry-After": str(exc.retry_after)})

    @app.exception_handler(FeatureOffError)
    async def feature_off(request: Request, exc: FeatureOffError) -> JSONResponse:
        body = error_body(
            ErrorCategory.EXTERNAL_API,
            exc.message,
            detail=exc.message,
            error_code="feature_off",
            retryable=False,
        )
        return _respond(request, 503, body)

    @app.exception_handler(AccountRestrictedError)
    async def account_restricted(request: Request, exc: AccountRestrictedError) -> JSONResponse:
        body = error_body(
            ErrorCategory.ACCOUNT,
            exc.message,
            detail=exc.message,
            retryable=False,
            extra={"account_state": exc.state},
        )
        return _respond(request, 403, body)

    @app.exception_handler(AuthenticationError)
    async def unauthenticated(request: Request, exc: AuthenticationError) -> JSONResponse:
        body = error_body(ErrorCategory.AUTHENTICATION, exc.message, detail=exc.message)
        return _respond(request, 401, body)

    @app.exception_handler(ForbiddenError)
    async def forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
        body = error_body(ErrorCategory.AUTHORIZATION, exc.message, detail=exc.message)
        return _respond(request, 403, body)

    @app.exception_handler(AiServiceError)
    async def ai_unavailable(request: Request, exc: AiServiceError) -> JSONResponse:
        """The AI provider could not answer. The app is fine; the message never carries provider text."""
        reference = diagnostics.new_reference_id()
        _log(request, ErrorCategory.EXTERNAL_API, exc.code or "ai_unavailable", 503, reference, exc)
        body = error_body(
            ErrorCategory.EXTERNAL_API,
            exc.message,
            detail=f"{exc.message} Reference: {reference}",
            error_code=exc.code,
            reference_id=reference,
            retryable=exc.retryable,
        )
        return _respond(request, 503, body)

    @app.exception_handler(InvalidInputError)
    async def invalid(request: Request, exc: InvalidInputError) -> JSONResponse:
        category = _domain_category(exc, ErrorCategory.VALIDATION)
        status = 413 if exc.code == "image_too_large" else 422
        body = error_body(
            category, exc.message, detail=_field_errors(exc, "business_rule"), error_code=exc.code
        )
        return _respond(request, status, body)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only where and why: never the submitted value, which could be anything the user typed.
        detail = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in jsonable_encoder(exc.errors())
        ]
        message = detail[0]["msg"] if detail else "Please check what you entered."
        return _respond(request, 422, error_body(ErrorCategory.VALIDATION, message, detail=detail))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        if status >= 500:
            return _unexpected(request, exc, ErrorCategory.UNEXPECTED, status)
        category = {
            401: ErrorCategory.AUTHENTICATION,
            403: ErrorCategory.AUTHORIZATION,
            404: ErrorCategory.NOT_FOUND,
        }.get(status, ErrorCategory.VALIDATION)
        message = (
            str(exc.detail)
            if isinstance(exc.detail, str)
            else MESSAGES.get(category, "This request cannot be done.")
        )
        body = error_body(category, message, detail=exc.detail if isinstance(exc.detail, str) else message)
        return _respond(request, status, body, dict(exc.headers or {}))

    @app.exception_handler(IntegrityError)
    async def integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        if "unique" in str(exc.orig).lower():  # a duplicate that slipped past a service's own check
            message = "This already exists."
            _log(request, ErrorCategory.DUPLICATE, "duplicate_record", 409, None, exc)
            body = error_body(ErrorCategory.DUPLICATE, message, detail=message)
            return _respond(request, 409, body)
        return _unexpected(request, exc, ErrorCategory.DATABASE, 500, retryable=False)

    @app.exception_handler(OperationalError)
    async def busy(request: Request, exc: OperationalError) -> JSONResponse:
        text = str(exc.orig).lower()
        transient = "locked" in text or "busy" in text or "timeout" in text or "connection" in text
        return _unexpected(
            request, exc, ErrorCategory.DATABASE, 503 if transient else 500, retryable=transient
        )

    @app.exception_handler(SQLAlchemyError)
    async def database(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return _unexpected(request, exc, ErrorCategory.DATABASE, 500, retryable=False)

    @app.exception_handler(DBAPIError)
    async def dbapi(request: Request, exc: DBAPIError) -> JSONResponse:
        return _unexpected(request, exc, ErrorCategory.DATABASE, 500, retryable=False)

    @app.exception_handler(TimeoutError)
    async def timed_out(request: Request, exc: TimeoutError) -> JSONResponse:
        return _unexpected(request, exc, ErrorCategory.TIMEOUT, 504)

    @app.exception_handler(Exception)
    async def anything_else(request: Request, exc: Exception) -> JSONResponse:
        return _unexpected(request, exc, ErrorCategory.UNEXPECTED, 500)
