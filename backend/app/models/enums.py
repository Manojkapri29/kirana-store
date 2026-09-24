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
    TASK_DRAFT = (
        "TASK_DRAFT"  # task_service.create: a business task, never anything financial or inventory-changing
    )
    CAMPAIGN_DRAFT = "CAMPAIGN_DRAFT"  # campaign_service.create: a DRAFT campaign, never launched


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


class MembershipStatus(StrEnum):
    """A person's standing in one shop. A membership is never deleted: REMOVED keeps the history."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class LoginStatus(StrEnum):
    """Whether a sign-in identity may sign in at all (separate from any shop's view of it)."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class StockCountScope(StrEnum):
    """What a stock count covers. A count of a single category still only lists that category's products."""

    FULL = "FULL"
    CATEGORY = "CATEGORY"
    PRODUCTS = "PRODUCTS"


class StockCountStatus(StrEnum):
    """DRAFT (scope chosen, expected quantities captured) -> COUNTING (counts being entered) -> REVIEW (all
    counted, differences visible) -> APPROVED (a second look confirmed the differences) -> POSTED
    (inventory_service has written the adjustments; irreversible). CANCELLED is possible from any state
    before POSTED and creates no stock movement."""

    DRAFT = "DRAFT"
    COUNTING = "COUNTING"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ReportSchedule(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CustomerType(StrEnum):
    """A classification the shop assigns, not a computed segment. Generic across every business type."""

    RETAIL = "RETAIL"
    WHOLESALE = "WHOLESALE"
    OTHER = "OTHER"


class CustomerSource(StrEnum):
    """How the customer first came to the shop. A fact recorded once, not inferred."""

    WALK_IN = "WALK_IN"
    REFERRAL = "REFERRAL"
    ONLINE = "ONLINE"
    CAMPAIGN = "CAMPAIGN"
    OTHER = "OTHER"


class LoyaltyEntryType(StrEnum):
    """Insert-only loyalty ledger entries (BUSINESS_RULES-style: never overwrite a balance, only add a row).
    Mirrors `CustomerLedgerEntryType`."""

    EARN = "EARN"
    REDEEM = "REDEEM"
    ADJUST = "ADJUST"
    EXPIRE = "EXPIRE"
    REVERSAL = "REVERSAL"


class CustomerGroupKind(StrEnum):
    MANUAL = "MANUAL"  # membership is an explicit list, never recalculated automatically
    RULE_BASED = "RULE_BASED"  # membership is a cached snapshot of a saved filter, recalculated on demand


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CampaignSendStatus(StrEnum):
    """The honest outcome of one attempted send to one customer. SENT only appears when a real provider for
    that channel is configured; otherwise the true reason is recorded — never a fabricated success."""

    SENT = "SENT"
    NOT_CONFIGURED = "NOT_CONFIGURED"  # no provider is set up for this channel
    SKIPPED_NO_CONSENT = "SKIPPED_NO_CONSENT"  # the customer never opted in to this channel
    SKIPPED_OPTED_OUT = "SKIPPED_OPTED_OUT"  # the customer explicitly opted out
    FAILED = "FAILED"


class AutomationTrigger(StrEnum):
    NEW_CUSTOMER = "NEW_CUSTOMER"
    INACTIVITY = "INACTIVITY"
    LOYALTY_MILESTONE = "LOYALTY_MILESTONE"
    PURCHASE_MILESTONE = "PURCHASE_MILESTONE"


class AutomationAction(StrEnum):
    CREATE_CAMPAIGN_DRAFT = "CREATE_CAMPAIGN_DRAFT"
    CREATE_TASK = "CREATE_TASK"
    NOTIFY = "NOTIFY"


class AutomationRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    SKIPPED_COOLDOWN = "SKIPPED_COOLDOWN"
    SKIPPED_CONDITION = "SKIPPED_CONDITION"
    FAILED = "FAILED"


class ReferralEventStatus(StrEnum):
    """PENDING (referred customer signed up, no qualifying purchase yet) -> QUALIFIED (the qualifying
    transaction happened) -> REWARDED (the reward was granted). EXPIRED and INVALID (e.g. self-referral,
    duplicate) never reward."""

    PENDING = "PENDING"
    QUALIFIED = "QUALIFIED"
    REWARDED = "REWARDED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
