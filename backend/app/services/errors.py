"""Errors raised by services. The API layer turns them into HTTP responses; services know nothing of HTTP.

`field` names the input the message belongs to (for example "sku"), so a form can show it next to the field.
"""

from typing import Any


class DomainError(Exception):
    """`code` is an optional machine-readable kind ("insufficient_stock", "duplicate_record", ...) so the API
    layer can tell apart problems that share an HTTP status. It is never the message shown to a person."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        code: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code
        self.data = data  # structured facts for the screen (for example the possible duplicates)


class NotFoundError(DomainError):
    """The thing does not exist, or belongs to another shop (the two are deliberately indistinguishable)."""


class ConflictError(DomainError):
    """The request is valid but clashes with existing data or the current state (duplicate SKU, ...).

    When several places clash at once (three sale lines are short of stock), `errors` lists every
    `(field, message)` pair so a screen can mark each one.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        errors: list[tuple[str | None, str]] | None = None,
        code: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, field=field, code=code, data=data)
        self.errors: list[tuple[str | None, str]] = errors or [(field, message)]


class EntitlementError(DomainError):
    """The shop's plan does not include this feature, or the plan's limit has been reached (HTTP 403).

    `feature` names the plan feature or limit that stopped the request, so a screen can offer an upgrade.
    """

    def __init__(self, message: str, *, feature: str) -> None:
        super().__init__(message)
        self.feature = feature


class InvalidInputError(DomainError):
    """The input breaks a business rule (unknown category, price above MRP in block mode, ...).

    Usually one problem. When several fields are wrong at once, `errors` lists all of them as
    `(field, message)` pairs so a form can mark every field in one go.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        errors: list[tuple[str | None, str]] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, field=field, code=code)
        self.errors: list[tuple[str | None, str]] = errors or [(field, message)]


class AiServiceError(DomainError):
    """The AI provider could not answer (unreachable, too slow, limiting requests, or unusable reply).

    The message is always the same safe sentence: nothing from the provider (its text, a status, a key) is
    ever kept
    here. `reason` says which of the cases it was, for the diagnostics log; `retryable` is true because trying
    again later can succeed. The rest of the application is unaffected.
    """

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__("AI Assistant is temporarily unavailable.", code=f"ai_{reason}")
        self.reason = reason
        self.retryable = retryable


class ForbiddenError(DomainError):
    """The request asks for something this user may not have (another shop's data, for example). HTTP 403."""


class RateLimitedError(DomainError):
    """Too many requests in a short time (HTTP 429). `retry_after` is how many seconds to wait."""

    def __init__(self, *, retry_after: int, group: str) -> None:
        super().__init__("Too many requests. Please wait a moment and try again.", code="rate_limited")
        self.retry_after = retry_after
        self.group = group


class FeatureOffError(DomainError):
    """The operator has switched this capability off for everyone (a feature flag). HTTP 503."""

    def __init__(self, feature: str) -> None:
        super().__init__(
            "This feature is temporarily turned off. The rest of the application is unaffected.",
            code="feature_off",
        )
        self.feature = feature


class AccountRestrictedError(DomainError):
    """The shop's account state does not allow this (suspended or deactivated). HTTP 403; message is safe."""

    def __init__(self, message: str, *, state: str) -> None:
        super().__init__(message, code="account_restricted")
        self.state = state
