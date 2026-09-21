"""How a customer pays a bill: shared by Detailed Sales and Quick Sales.

Pure rules, no database. One implementation of "paid in full, or part now and the rest on credit":

  * `amount_paid=None` means paid in full. Less than the total is a credit sale; more is refused (the sale
    models have no overpayment: extra money is an advance, a payment on the customer's khata).
  * Any money received needs a payment method. A credit sale needs a customer, who is charged through
    `khata_service` (never here).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models.enums import PaymentMethod
from app.services import sale_calculation as calc
from app.services.errors import InvalidInputError

CENT = Decimal("0.01")


def money(value: Any, *, field_name: str) -> Decimal:
    """A non-negative amount with at most two decimals (the API already checks; services are used directly
    too). Floats and text are refused so no binary rounding can creep in."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal | int):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field_name)
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise InvalidInputError("The amount cannot be negative.", field=field_name)
    if amount != amount.quantize(CENT):
        raise InvalidInputError("Use at most 2 decimal places.", field=field_name)
    return amount.quantize(CENT)


@dataclass(frozen=True)
class Payment:
    split: calc.PaymentSplit
    method: PaymentMethod | None
    reference: str | None


def resolve_payment(
    total: Decimal,
    customer_id: int | None,
    amount_paid: Decimal | None,
    method: PaymentMethod | None,
    reference: str | None,
    *,
    total_field: str = "items",
) -> Payment:
    """Decide paid or credit for a bill of `total`, or refuse with a message naming the field at fault."""
    if total <= 0:
        raise InvalidInputError("The bill total must be greater than zero.", field=total_field)
    paid = None if amount_paid is None else money(amount_paid, field_name="amount_paid")
    if paid is not None and paid > total:
        raise InvalidInputError(
            f"The amount paid is more than the bill total ({total:.2f}). To keep extra money as an advance, "
            "take a payment on the customer's khata instead.",
            field="amount_paid",
        )
    split = calc.split_payment(total, paid)
    if split.paid > 0 and method is None:
        raise InvalidInputError("Choose how the customer paid (cash, UPI or other).", field="payment_method")
    if split.credit > 0 and customer_id is None:
        raise InvalidInputError(
            "Choose the customer: paying less than the total leaves an amount on their khata.",
            field="customer_id",
        )
    clean = (reference or "").strip() or None
    if clean is not None and len(clean) > 100:
        raise InvalidInputError(
            "The payment reference is too long (100 characters at most).", field="payment_reference"
        )
    return Payment(split, method if split.paid > 0 else None, clean)
