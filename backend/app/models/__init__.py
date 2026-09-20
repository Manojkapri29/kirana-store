"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`,
which Alembic uses to compare the models with the database."""

from app.models.base import Base
from app.models.catalog import Category, Product, Unit
from app.models.expenses import Expense, ExpenseCategory
from app.models.inventory import InventoryTransaction
from app.models.khata import CustomerLedgerEntry
from app.models.parties import Customer, Supplier
from app.models.purchasing import Purchase, PurchaseItem, PurchaseReturn, PurchaseReturnItem
from app.models.sales import QuickSale, Sale, SaleItem, SalesReturn, SalesReturnItem
from app.models.shop import BusinessType, Shop, User
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
    "Product",
    "Purchase",
    "PurchaseItem",
    "PurchaseReturn",
    "PurchaseReturnItem",
    "QuickSale",
    "Sale",
    "SaleItem",
    "SalesReturn",
    "SalesReturnItem",
    "Shop",
    "Supplier",
    "Unit",
    "User",
]
