"""Allowed values for constrained columns.

Each enum is stored as text and guarded by a database CHECK constraint, so an invalid value is
rejected even if it reaches the database through raw SQL. Member names equal their stored values.
"""

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "OWNER"
    STAFF = "STAFF"


class Language(StrEnum):
    EN = "en"
    HI = "hi"


class MrpValidationMode(StrEnum):
    """What to do when a selling price exceeds MRP. Chosen per shop (BUSINESS_RULES P2)."""

    WARN = "WARN"
    BLOCK = "BLOCK"


class InventoryTxnType(StrEnum):
    OPENING = "OPENING"
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    SALE_RETURN = "SALE_RETURN"
    PURCHASE_RETURN = "PURCHASE_RETURN"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class AdjustmentReason(StrEnum):
    """Mandatory for ADJUSTMENT ledger rows (BUSINESS_RULES A1). OTHER additionally requires a note."""

    CUSTOMER_RETURN_NO_BILL = "CUSTOMER_RETURN_NO_BILL"
    COUNT_CORRECTION = "COUNT_CORRECTION"
    DAMAGED = "DAMAGED"
    EXPIRED = "EXPIRED"
    LOST = "LOST"
    OTHER = "OTHER"


class StockReferenceType(StrEnum):
    """The document a stock-ledger row came from. `PRODUCT` is used for opening stock."""

    PRODUCT = "PRODUCT"
    PURCHASE_ITEM = "PURCHASE_ITEM"
    SALE_ITEM = "SALE_ITEM"
    PURCHASE_RETURN_ITEM = "PURCHASE_RETURN_ITEM"
    SALES_RETURN_ITEM = "SALES_RETURN_ITEM"


class CustomerLedgerEntryType(StrEnum):
    OPENING_BALANCE = "OPENING_BALANCE"
    CREDIT_SALE = "CREDIT_SALE"
    PAYMENT = "PAYMENT"
    RETURN_CREDIT = "RETURN_CREDIT"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class KhataReferenceType(StrEnum):
    """The document a customer-ledger row came from."""

    SALE = "SALE"
    QUICK_SALE = "QUICK_SALE"
    SALES_RETURN = "SALES_RETURN"


class DocumentStatus(StrEnum):
    POSTED = "POSTED"
    VOID = "VOID"


class SaleStatus(StrEnum):
    """A sale starts as a DRAFT (a cart: no effect on stock, khata or revenue), becomes POSTED (stock goes
    out, credit is charged to the customer's khata), and can later be VOIDED (both are reversed)."""

    DRAFT = "DRAFT"
    POSTED = "POSTED"
    VOID = "VOID"


class PurchaseStatus(StrEnum):
    """A purchase starts as a DRAFT (no effect on stock), becomes POSTED (stock and cost change), and can
    later be VOIDED (its stock effect is reversed). Only POSTED purchases affect inventory."""

    DRAFT = "DRAFT"
    POSTED = "POSTED"
    VOID = "VOID"


class PaymentType(StrEnum):
    PAID = "PAID"
    CREDIT = "CREDIT"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    UPI = "UPI"
    OTHER = "OTHER"


class RefundMode(StrEnum):
    """How a sales return is settled. KHATA reduces the customer's outstanding balance."""

    CASH = "CASH"
    UPI = "UPI"
    KHATA = "KHATA"


class SupplierCreditMode(StrEnum):
    """How a purchase return is settled."""

    CASH = "CASH"
    UPI = "UPI"
    SUPPLIER_CREDIT = "SUPPLIER_CREDIT"
