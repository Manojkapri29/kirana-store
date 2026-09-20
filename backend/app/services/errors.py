"""Errors raised by services. The API layer turns them into HTTP responses; services know nothing of HTTP.

`field` names the input the message belongs to (for example "sku"), so a form can show it next to the field.
"""


class DomainError(Exception):
    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(DomainError):
    """The thing does not exist, or belongs to another shop (the two are deliberately indistinguishable)."""


class ConflictError(DomainError):
    """The request is valid but clashes with existing data or the current state (duplicate SKU, ...)."""


class InvalidInputError(DomainError):
    """The input breaks a business rule (unknown category, price above MRP in block mode, ...)."""
