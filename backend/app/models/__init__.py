"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`,
which Alembic uses to compare the models with the database."""

from app.models.access import Account, AuthSession, Invitation, Role, RolePermission
from app.models.ai import AiAction, AiUsage
from app.models.approvals import ApprovalRequest
from app.models.automation import AutomationRule, AutomationRun
from app.models.base import Base
from app.models.campaigns import Campaign, CampaignAudienceSnapshot, CampaignSend
from app.models.catalog import Category, Product, Unit
from app.models.crm import CustomerGroup, CustomerGroupMember, CustomerNote
from app.models.expenses import Expense, ExpenseCategory
from app.models.finance import (
    CashCount,
    FinanceEntry,
    FinanceSettings,
    FinancialPeriod,
    ReconciliationMark,
    TaxRate,
    TaxSetting,
)
from app.models.inventory import InventoryTransaction
from app.models.jobs import BackgroundJob
from app.models.khata import CustomerLedgerEntry
from app.models.loyalty import LoyaltyLedger, LoyaltyProgram
from app.models.notifications import NotificationDelivery, NotificationEvent, NotificationPreference
from app.models.operations import (
    AdminAuditLog,
    BackupRecord,
    RestoreRecord,
    SupportAccessGrant,
    SystemAdmin,
    SystemEvent,
)
from app.models.parties import Customer, Supplier
from app.models.pricing import PriceObservation, ProductImage
from app.models.promotion import Promotion, SalePromotion
from app.models.purchasing import Purchase, PurchaseItem, PurchaseReturn, PurchaseReturnItem
from app.models.referrals import ReferralCode, ReferralEvent, ReferralProgram
from app.models.sales import QuickSale, Sale, SaleItem, SalesReturn, SalesReturnItem
from app.models.scheduled_reports import ScheduledReport
from app.models.shop import BusinessType, Shop, User
from app.models.stock_count import StockCount, StockCountItem
from app.models.subscription import Plan, PlanFeature, ShopSubscription, SubscriptionUsage
from app.models.system import AuditLog, DocumentSequence, IdempotencyKey
from app.models.tasks import BusinessTask, TaskComment

__all__ = [
    "Account",
    "ApprovalRequest",
    "AuthSession",
    "AutomationRule",
    "AutomationRun",
    "BackgroundJob",
    "BusinessTask",
    "Campaign",
    "CampaignAudienceSnapshot",
    "CampaignSend",
    "CustomerGroup",
    "CustomerGroupMember",
    "CustomerNote",
    "Invitation",
    "LoyaltyLedger",
    "LoyaltyProgram",
    "ReferralCode",
    "ReferralEvent",
    "ReferralProgram",
    "Role",
    "RolePermission",
    "ScheduledReport",
    "StockCount",
    "StockCountItem",
    "TaskComment",
    "AdminAuditLog",
    "BackupRecord",
    "NotificationDelivery",
    "NotificationEvent",
    "NotificationPreference",
    "RestoreRecord",
    "SupportAccessGrant",
    "SystemAdmin",
    "SystemEvent",
    "AiAction",
    "AiUsage",
    "AuditLog",
    "Base",
    "BusinessType",
    "Category",
    "Customer",
    "CustomerLedgerEntry",
    "DocumentSequence",
    "CashCount",
    "Expense",
    "FinanceEntry",
    "FinanceSettings",
    "FinancialPeriod",
    "ReconciliationMark",
    "TaxRate",
    "TaxSetting",
    "ExpenseCategory",
    "IdempotencyKey",
    "InventoryTransaction",
    "Plan",
    "PlanFeature",
    "PriceObservation",
    "Product",
    "ProductImage",
    "Promotion",
    "Purchase",
    "PurchaseItem",
    "PurchaseReturn",
    "PurchaseReturnItem",
    "QuickSale",
    "Sale",
    "SaleItem",
    "SalePromotion",
    "SalesReturn",
    "SalesReturnItem",
    "Shop",
    "ShopSubscription",
    "SubscriptionUsage",
    "Supplier",
    "Unit",
    "User",
]
