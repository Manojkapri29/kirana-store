"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`,
which Alembic uses to compare the models with the database."""

from app.models.base import Base
from app.models.catalog import Category, Product, Unit
from app.models.expenses import Expense, ExpenseCategory
from app.models.inventory import InventoryTransaction
from app.models.khata import CustomerLedgerEntry
from app.models.parties import Customer, Supplier
from app.models.pricing import PriceObservation, ProductImage
from app.models.promotion import Promotion, SalePromotion
from app.models.purchasing import Purchase, PurchaseItem, PurchaseReturn, PurchaseReturnItem
from app.models.sales import QuickSale, Sale, SaleItem, SalesReturn, SalesReturnItem
from app.models.shop import BusinessType, Shop, User
from app.models.subscription import Plan, PlanFeature, ShopSubscription, SubscriptionUsage
from app.models.system import AuditLog, DocumentSequence, IdempotencyKey

__all__ = [
    "AuditLog",
    "Base",
    "BusinessType",
    "Category",
    "Customer",
    "CustomerLedgerEntry",
    "DocumentSequence",
    "Expense",
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
