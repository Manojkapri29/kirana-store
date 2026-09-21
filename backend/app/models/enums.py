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


class BillingInterval(StrEnum):
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


class SubscriptionStatus(StrEnum):
    """TRIAL and ACTIVE are "current": they give the shop its plan while they last. CANCELLED and EXPIRED are
    history. A current subscription past its end date counts as expired without needing a status change."""

    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


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


class PromotionType(StrEnum):
    """What a promotion gives. Coupons, first-order and customer-specific offers are not types: they are the
    same benefit reached through `coupon_code` or `audience`, so there is one discount system, not several."""

    PERCENT = "PERCENT"  # a percentage off the eligible amount
    AMOUNT = "AMOUNT"  # a fixed amount off the eligible amount
    OFFER_PRICE = "OFFER_PRICE"  # a promotional price per unit for the chosen products
    BUY_X_GET_Y = (
        "BUY_X_GET_Y"  # buy X units, get Y units of the chosen products free (or at a percentage off)
    )


class PromotionScope(StrEnum):
    """Which part of the cart a promotion looks at."""

    CART = "CART"  # the whole bill
    PRODUCTS = "PRODUCTS"  # only the chosen products
    CATEGORIES = "CATEGORIES"  # only products in the chosen categories


class AiActionKind(StrEnum):
    """What an AI-prepared action would do once a person confirms it. Each uses an existing service."""

    PURCHASE_DRAFT = "PURCHASE_DRAFT"  # purchase_service.create_purchase: a DRAFT, never posted
    STOCK_ADJUSTMENT = "STOCK_ADJUSTMENT"  # inventory_service.record_adjustment with a reason code
    PROMOTION_DRAFT = "PROMOTION_DRAFT"  # promotion_service.create_promotion: a DRAFT, never activated


class AiActionStatus(StrEnum):
    """PROPOSED -> EXECUTED, or CANCELLED. FAILED (the service refused or broke) can be edited and retried."""

    PROPOSED = "PROPOSED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PromotionStatus(StrEnum):
    """DRAFT (being set up) -> ACTIVE <-> PAUSED -> EXPIRED. A promotion is never deleted."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"


class PromotionAudience(StrEnum):
    ALL = "ALL"
    NEW_CUSTOMER = "NEW_CUSTOMER"  # a customer with no earlier posted sale (from the shop's own history)
    CUSTOMERS = "CUSTOMERS"  # only the listed customers


class AccountStatus(StrEnum):
    """A shop's account state (SaaS operations). Data is never deleted by a change of state."""

    ACTIVE = "ACTIVE"
    TRIAL = "TRIAL"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class AdminRole(StrEnum):
    """System-level roles (not a shop's OWNER or STAFF)."""

    SUPER_ADMIN = "SUPER_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"
    OPERATIONS_ADMIN = "OPERATIONS_ADMIN"


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class BackupStatus(StrEnum):
    CREATED = "CREATED"  # the file exists but has not been verified
    VERIFIED = "VERIFIED"  # integrity check, checksum and schema revision all confirmed
    FAILED = "FAILED"
    DELETED = "DELETED"  # removed by retention; the record stays


class BackupKind(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    PRE_RESTORE = "PRE_RESTORE"  # the safety copy taken just before a restore


class RestoreMode(StrEnum):
    VALIDATE = "VALIDATE"
    REHEARSAL = "REHEARSAL"
    RESTORE = "RESTORE"


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    PUSH = "PUSH"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
