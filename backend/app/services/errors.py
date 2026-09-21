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
