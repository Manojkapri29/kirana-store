"""Permissions: the catalogue, the default role sets, and which permission each API route needs.

This is DATA about authorization, not the decisions. The decisions are made by `authorization_service` (who is the caller,
is the membership active, does the role hold the permission). Three things live here:

1. `PERMISSIONS`: every permission code, with a plain description and a group for screens.
2. `SYSTEM_ROLES`: the default set of each system role. These are defaults: they are copied into the `role_permissions`
   table by migration 0015 and read from there at request time, so they can be changed without a code change. OWNER is
   special: it always holds every permission in the catalogue (a permission added later is the owner's at once).
3. `ROUTE_RULES`: the permission(s) each API route requires. One table, checked in one place (`api/deps.py`), so a
   route cannot be added and forgotten: `tests/test_rbac_routes.py` fails if a route has no rule.
"""

import re
from dataclasses import dataclass

# code -> (group, description)
PERMISSIONS: dict[str, tuple[str, str]] = {
    "PRODUCT_VIEW": ("Products", "See products and categories"),
    "PRODUCT_CREATE": ("Products", "Add products and categories"),
    "PRODUCT_EDIT": ("Products", "Change products, categories and product photos"),
    "PRODUCT_DEACTIVATE": ("Products", "Deactivate and reactivate products"),
    "INVENTORY_VIEW": ("Inventory", "See stock and stock history"),
    "INVENTORY_ADJUST": ("Inventory", "Record opening stock and stock adjustments"),
    "INVENTORY_EXPORT": ("Inventory", "Download stock and stock-history files"),
    "SUPPLIER_VIEW": ("Suppliers", "See suppliers"),
    "SUPPLIER_CREATE": ("Suppliers", "Add suppliers"),
    "SUPPLIER_EDIT": ("Suppliers", "Change, deactivate and reactivate suppliers"),
    "PURCHASE_VIEW": ("Purchases", "See purchases and purchase returns (they show costs)"),
    "PURCHASE_CREATE": ("Purchases", "Create and edit purchase drafts"),
    "PURCHASE_POST": ("Purchases", "Post purchases (this changes stock and costs)"),
    "PURCHASE_VOID": ("Purchases", "Void and correct posted purchases"),
    "SALE_VIEW": ("Sales", "See sales, quick sales and sales returns"),
    "SALE_CREATE": ("Sales", "Create and edit sale drafts and check totals"),
    "SALE_POST": ("Sales", "Post sales (this changes stock and khata)"),
    "SALE_VOID": ("Sales", "Void and correct posted sales, quick sales and returns"),
    "QUICK_SALE_CREATE": ("Sales", "Record and post quick sales"),
    "RETURN_CREATE": ("Returns", "Prepare returns"),
    "RETURN_POST": ("Returns", "Post returns (this changes stock and refunds)"),
    "CUSTOMER_VIEW": ("Customers", "See customers"),
    "CUSTOMER_CREATE": ("Customers", "Add customers"),
    "CUSTOMER_EDIT": ("Customers", "Change, deactivate and reactivate customers"),
    "KHATA_VIEW": ("Khata", "See customer balances and khata history"),
    "KHATA_PAYMENT": ("Khata", "Record payments received"),
    "KHATA_ADJUST": ("Khata", "Record opening balances, adjustments and reversals"),
    "PROMOTION_VIEW": ("Offers", "See offers and coupons"),
    "PROMOTION_CREATE": ("Offers", "Create offers and coupons"),
    "PROMOTION_EDIT": ("Offers", "Change, activate, pause and expire offers"),
    "ONLINE_ORDER_VIEW": ("Online orders", "See online orders (there is no online store yet)"),
    "ONLINE_ORDER_ACCEPT": ("Online orders", "Accept online orders"),
    "ONLINE_ORDER_REJECT": ("Online orders", "Reject online orders"),
    "ONLINE_ORDER_STATUS_UPDATE": ("Online orders", "Move an online order along"),
    "REPORT_VIEW": ("Reports", "See reports, insights and the business overview"),
    "REPORT_EXPORT": ("Reports", "Download data files (customers, sales, purchases, reports)"),
    "AI_USE": ("AI", "Use the business assistant and read documents"),
    "AI_ACTION_CONFIRM": ("AI", "Confirm actions the assistant proposes"),
    "PRICE_INTELLIGENCE_USE": ("Products", "Look up outside prices"),
    "STORE_SETTINGS_MANAGE": ("Settings", "Change online store settings"),
    "STAFF_VIEW": ("Staff", "See staff and their access"),
    "STAFF_INVITE": ("Staff", "Invite staff"),
    "STAFF_EDIT": ("Staff", "Change a staff member's role"),
    "STAFF_SUSPEND": ("Staff", "Suspend, reactivate and remove staff"),
    "ROLE_MANAGE": ("Staff", "Create and change custom roles"),
    "SUBSCRIPTION_VIEW": ("Account", "See the plan and its usage"),
    "BACKUP_VIEW": ("Account", "See backup status"),
    "BACKUP_CREATE": ("Account", "Start a backup"),
    "AUDIT_LOG_VIEW": ("Account", "See who did what in this shop"),
    "BUSINESS_SETTINGS_MANAGE": ("Settings", "Change the shop's details and settings"),
    "STOCK_COUNT_VIEW": ("Stock counting", "See stock counts and their differences"),
    "STOCK_COUNT_CREATE": (
        "Stock counting",
        "Start a stock count, choose its scope and enter counted quantities",
    ),
    "STOCK_COUNT_REVIEW": ("Stock counting", "Submit a count's differences for approval"),
    "STOCK_COUNT_APPROVE": (
        "Stock counting",
        "Approve a count's differences (not the person who created it)",
    ),
    "STOCK_COUNT_POST": ("Stock counting", "Post an approved count: this writes the stock adjustments"),
    "TASK_VIEW": ("Tasks", "See tasks"),
    "TASK_CREATE": ("Tasks", "Create a task and comment on it"),
    "TASK_ASSIGN": ("Tasks", "Assign a task to someone"),
    "TASK_COMPLETE": ("Tasks", "Mark a task done or reopen it"),
    "TASK_CANCEL": ("Tasks", "Cancel a task"),
    "SCHEDULED_REPORT_MANAGE": ("Reports", "Create, change and remove scheduled reports"),
    "CRM_VIEW": ("CRM", "See customer profiles, timelines and segments"),
    "CRM_MANAGE": ("CRM", "Add notes, classify customers and change communication preferences"),
    "CRM_SEGMENT_MANAGE": ("CRM", "Create and change customer groups"),
    "CRM_ANALYTICS_VIEW": ("CRM", "See the CRM dashboard, retention and growth analytics"),
    "LOYALTY_VIEW": ("Loyalty", "See loyalty balances and the loyalty ledger"),
    "LOYALTY_MANAGE": ("Loyalty", "Configure the loyalty program and record manual adjustments"),
    "CAMPAIGN_VIEW": ("Campaigns", "See campaigns and their audience"),
    "CAMPAIGN_MANAGE": ("Campaigns", "Create, change, pause and cancel campaigns"),
    "CAMPAIGN_LAUNCH": ("Campaigns", "Launch a campaign (send it to its audience)"),
    "AUTOMATION_MANAGE": ("Campaigns", "Create and change marketing automation rules"),
    "REFERRAL_VIEW": ("Referrals", "See referral codes and referral events"),
    "REFERRAL_MANAGE": ("Referrals", "Configure the referral program"),
}

ALL_PERMISSIONS = frozenset(PERMISSIONS)

# Intentionally owner-only: a custom role can never hold these, and no default role except OWNER does.
OWNER_ONLY_PERMISSIONS = frozenset({"ROLE_MANAGE", "BACKUP_CREATE"})

_VIEW_ALL = {p for p in ALL_PERMISSIONS if p.endswith("_VIEW")}

SYSTEM_ROLES: dict[str, tuple[str, str, frozenset[str]]] = {
    # code: (name, description, permissions). OWNER is resolved to the whole catalogue; the set here is for seeding.
    "OWNER": ("Owner", "Runs the shop. Holds every permission.", ALL_PERMISSIONS),
    "MANAGER": (
        "Manager",
        "Runs the shop day to day. Everything except the owner-only actions.",
        ALL_PERMISSIONS - OWNER_ONLY_PERMISSIONS,
    ),
    "CASHIER": (
        "Cashier",
        "Bills customers, records quick sales and payments.",
        frozenset(
            {
                "PRODUCT_VIEW",
                "INVENTORY_VIEW",
                "SALE_VIEW",
                "SALE_CREATE",
                "SALE_POST",
                "QUICK_SALE_CREATE",
                "CUSTOMER_VIEW",
                "CUSTOMER_CREATE",
                "KHATA_VIEW",
                "KHATA_PAYMENT",
                "PROMOTION_VIEW",
                "TASK_VIEW",
                "TASK_CREATE",
                "TASK_COMPLETE",
                "CRM_VIEW",
                "LOYALTY_VIEW",
            }
        ),
    ),
    "INVENTORY_STAFF": (
        "Inventory staff",
        "Looks after products and stock. No financial reports.",
        frozenset(
            {
                "PRODUCT_VIEW",
                "PRODUCT_CREATE",
                "PRODUCT_EDIT",
                "INVENTORY_VIEW",
                "INVENTORY_ADJUST",
                "INVENTORY_EXPORT",
                "SUPPLIER_VIEW",
                "STOCK_COUNT_VIEW",
                "STOCK_COUNT_CREATE",
                "STOCK_COUNT_REVIEW",
                "TASK_VIEW",
                "TASK_CREATE",
                "TASK_COMPLETE",
            }
        ),
    ),
    "SALES_STAFF": (
        "Sales staff",
        "Sells and looks after customers and simple order handling.",
        frozenset(
            {
                "PRODUCT_VIEW",
                "INVENTORY_VIEW",
                "SALE_VIEW",
                "SALE_CREATE",
                "SALE_POST",
                "QUICK_SALE_CREATE",
                "CUSTOMER_VIEW",
                "CUSTOMER_CREATE",
                "CUSTOMER_EDIT",
                "KHATA_VIEW",
                "PROMOTION_VIEW",
                "ONLINE_ORDER_VIEW",
                "ONLINE_ORDER_ACCEPT",
                "ONLINE_ORDER_REJECT",
                "ONLINE_ORDER_STATUS_UPDATE",
                "TASK_VIEW",
                "TASK_CREATE",
                "TASK_COMPLETE",
                "CRM_VIEW",
                "CRM_MANAGE",
                "LOYALTY_VIEW",
            }
        ),
    ),
    "ACCOUNTANT": (
        "Accountant",
        "Purchases, khata, financial reports and exports. No stock adjustments.",
        frozenset(
            {
                "PRODUCT_VIEW",
                "INVENTORY_VIEW",
                "SUPPLIER_VIEW",
                "PURCHASE_VIEW",
                "PURCHASE_CREATE",
                "PURCHASE_POST",
                "SALE_VIEW",
                "CUSTOMER_VIEW",
                "KHATA_VIEW",
                "KHATA_PAYMENT",
                "KHATA_ADJUST",
                "REPORT_VIEW",
                "REPORT_EXPORT",
                "AUDIT_LOG_VIEW",
                "TASK_VIEW",
                "TASK_CREATE",
                "TASK_COMPLETE",
                "CRM_VIEW",
                "LOYALTY_VIEW",
                "CRM_ANALYTICS_VIEW",
            }
        ),
    ),
}

# What a request context built WITHOUT a membership (a test, or the development shortcut) may do when its role is STAFF.
# Real sessions never use this: they read their role's set from the database.
LEGACY_STAFF_PERMISSIONS = frozenset(
    _VIEW_ALL - {"STAFF_VIEW", "SUBSCRIPTION_VIEW", "BACKUP_VIEW", "AUDIT_LOG_VIEW"}
    | {
        "PRODUCT_CREATE",
        "PRODUCT_EDIT",
        "SUPPLIER_CREATE",
        "SUPPLIER_EDIT",
        "PURCHASE_CREATE",
        "PURCHASE_POST",
        "SALE_CREATE",
        "SALE_POST",
        "QUICK_SALE_CREATE",
        "RETURN_CREATE",
        "RETURN_POST",
        "CUSTOMER_CREATE",
        "CUSTOMER_EDIT",
        "KHATA_PAYMENT",
        "INVENTORY_ADJUST",
        "PRICE_INTELLIGENCE_USE",
    }
)


# --- Which permission each route needs -------------------------------------------------------------------------------

MEMBER = "MEMBER"  # any active member of the shop (no specific permission)


@dataclass(frozen=True)
class Rule:
    method: str
    template: str  # relative to /api/v1, e.g. /customers/{customer_id}/payments
    needs: tuple[str, ...]  # ALL of these are required; ("MEMBER",) means any active member


def _r(method: str, template: str, *needs: str) -> Rule:
    return Rule(method, template, needs or (MEMBER,))


ROUTE_RULES: tuple[Rule, ...] = (
    # Shop, reference data, account
    _r("GET", "/shop"),
    _r("PATCH", "/shop", "BUSINESS_SETTINGS_MANAGE"),
    _r("GET", "/shop/template"),
    _r("GET", "/units"),
    _r("GET", "/business-types"),
    _r("GET", "/categories", "PRODUCT_VIEW"),
    _r("POST", "/categories", "PRODUCT_CREATE"),
    _r("PATCH", "/categories/{category_id}", "PRODUCT_EDIT"),
    _r("GET", "/account"),
    _r("GET", "/account/usage", "SUBSCRIPTION_VIEW"),
    _r("GET", "/account/backup-status", "BACKUP_VIEW"),
    _r("GET", "/account/health", "REPORT_VIEW"),
    _r("GET", "/subscription", "SUBSCRIPTION_VIEW"),
    _r("GET", "/audit-log", "AUDIT_LOG_VIEW"),
    _r("GET", "/analytics/overview", "REPORT_VIEW"),
    # Notifications belong to the person, not the business
    _r("GET", "/notifications"),
    _r("GET", "/notifications/unread-count"),
    _r("POST", "/notifications/read-all"),
    _r("POST", "/notifications/refresh-alerts"),
    _r("GET", "/notifications/preferences"),
    _r("PUT", "/notifications/preferences/{category}"),
    _r("POST", "/notifications/{delivery_id}/read"),
    # Products
    _r("GET", "/products", "PRODUCT_VIEW"),
    _r("POST", "/products", "PRODUCT_CREATE"),
    _r("GET", "/products/lookup", "PRODUCT_VIEW"),
    _r("GET", "/products/{product_id}", "PRODUCT_VIEW"),
    _r("PATCH", "/products/{product_id}", "PRODUCT_EDIT"),
    _r("POST", "/products/{product_id}/activate", "PRODUCT_DEACTIVATE"),
    _r("POST", "/products/{product_id}/deactivate", "PRODUCT_DEACTIVATE"),
    _r("POST", "/image-intelligence/analyze", "PRODUCT_CREATE"),
    _r("POST", "/image-intelligence/confirm-product", "PRODUCT_CREATE"),
    _r("GET", "/image-intelligence/status", "PRODUCT_VIEW"),
    _r("GET", "/image-intelligence/products/{product_id}/image", "PRODUCT_VIEW"),
    _r("PUT", "/image-intelligence/products/{product_id}/image", "PRODUCT_EDIT"),
    _r("POST", "/image-intelligence/products/{product_id}/image/remove", "PRODUCT_EDIT"),
    _r("POST", "/price-intelligence/check", "PRICE_INTELLIGENCE_USE"),
    _r("GET", "/price-intelligence/history", "PRICE_INTELLIGENCE_USE"),
    _r("GET", "/price-intelligence/providers", "PRICE_INTELLIGENCE_USE"),
    # Inventory
    _r("GET", "/inventory", "INVENTORY_VIEW"),
    _r("GET", "/inventory/transactions", "INVENTORY_VIEW"),
    _r("GET", "/inventory/products/{product_id}", "INVENTORY_VIEW"),
    _r("GET", "/inventory/products/{product_id}/transactions", "INVENTORY_VIEW"),
    _r("POST", "/inventory/opening-stock", "INVENTORY_ADJUST"),
    # Suppliers
    _r("GET", "/suppliers", "SUPPLIER_VIEW"),
    _r("GET", "/suppliers/options", "SUPPLIER_VIEW"),
    _r("POST", "/suppliers", "SUPPLIER_CREATE"),
    _r("GET", "/suppliers/{supplier_id}", "SUPPLIER_VIEW"),
    _r("PATCH", "/suppliers/{supplier_id}", "SUPPLIER_EDIT"),
    _r("POST", "/suppliers/{supplier_id}/activate", "SUPPLIER_EDIT"),
    _r("POST", "/suppliers/{supplier_id}/deactivate", "SUPPLIER_EDIT"),
    # Purchases
    _r("GET", "/purchases", "PURCHASE_VIEW"),
    _r("POST", "/purchases", "PURCHASE_CREATE"),
    _r("GET", "/purchases/supplier-totals/{supplier_id}", "PURCHASE_VIEW"),
    _r("GET", "/purchases/{purchase_id}", "PURCHASE_VIEW"),
    _r("PATCH", "/purchases/{purchase_id}", "PURCHASE_CREATE"),
    _r("POST", "/purchases/{purchase_id}/items", "PURCHASE_CREATE"),
    _r("PUT", "/purchases/{purchase_id}/items", "PURCHASE_CREATE"),
    _r("PATCH", "/purchases/{purchase_id}/items/{item_id}", "PURCHASE_CREATE"),
    _r("POST", "/purchases/{purchase_id}/post", "PURCHASE_POST"),
    _r("POST", "/purchases/{purchase_id}/void", "PURCHASE_VOID"),
    _r("POST", "/purchases/{purchase_id}/correct", "PURCHASE_VOID", "PURCHASE_CREATE"),
    # Sales
    _r("GET", "/sales", "SALE_VIEW"),
    _r("POST", "/sales", "SALE_CREATE"),
    _r("POST", "/sales/calculate", "SALE_CREATE"),
    _r("GET", "/sales/{sale_id}", "SALE_VIEW"),
    _r("PATCH", "/sales/{sale_id}", "SALE_CREATE"),
    _r("PUT", "/sales/{sale_id}/items", "SALE_CREATE"),
    _r("POST", "/sales/{sale_id}/post", "SALE_POST"),
    _r("POST", "/sales/{sale_id}/void", "SALE_VOID"),
    _r("POST", "/sales/{sale_id}/correct", "SALE_VOID", "SALE_CREATE"),
    _r("GET", "/quick-sales", "SALE_VIEW"),
    _r("POST", "/quick-sales", "QUICK_SALE_CREATE"),
    _r("GET", "/quick-sales/{quick_sale_id}", "SALE_VIEW"),
    _r("PATCH", "/quick-sales/{quick_sale_id}", "QUICK_SALE_CREATE"),
    _r("POST", "/quick-sales/{quick_sale_id}/post", "QUICK_SALE_CREATE"),
    _r("POST", "/quick-sales/{quick_sale_id}/void", "SALE_VOID"),
    # Returns
    _r("GET", "/sales-returns", "SALE_VIEW"),
    _r("GET", "/sales-returns/{return_id}", "SALE_VIEW"),
    _r("POST", "/sales-returns/calculate", "RETURN_CREATE"),
    _r("POST", "/sales-returns", "RETURN_CREATE", "RETURN_POST"),
    _r("POST", "/sales-returns/{return_id}/void", "SALE_VOID"),
    _r("GET", "/purchase-returns", "PURCHASE_VIEW"),
    _r("GET", "/purchase-returns/{return_id}", "PURCHASE_VIEW"),
    _r("POST", "/purchase-returns/calculate", "RETURN_CREATE"),
    _r("POST", "/purchase-returns", "RETURN_CREATE", "RETURN_POST"),
    _r("POST", "/purchase-returns/{return_id}/void", "PURCHASE_VOID"),
    # Customers and khata
    _r("GET", "/customers", "CUSTOMER_VIEW"),
    _r("POST", "/customers", "CUSTOMER_CREATE"),
    _r("GET", "/customers/{customer_id}", "CUSTOMER_VIEW"),
    _r("PATCH", "/customers/{customer_id}", "CUSTOMER_EDIT"),
    _r("POST", "/customers/{customer_id}/activate", "CUSTOMER_EDIT"),
    _r("POST", "/customers/{customer_id}/deactivate", "CUSTOMER_EDIT"),
    _r("GET", "/customers/{customer_id}/balance", "KHATA_VIEW"),
    _r("GET", "/customers/{customer_id}/ledger", "KHATA_VIEW"),
    _r("POST", "/customers/{customer_id}/payments", "KHATA_PAYMENT"),
    _r("POST", "/customers/{customer_id}/adjustments", "KHATA_ADJUST"),
    _r("POST", "/customers/{customer_id}/opening-balance", "KHATA_ADJUST"),
    _r("POST", "/customers/{customer_id}/ledger/{entry_id}/reverse", "KHATA_ADJUST"),
    # Offers
    _r("GET", "/promotions", "PROMOTION_VIEW"),
    _r("GET", "/promotions/usage", "PROMOTION_VIEW"),
    _r("POST", "/promotions", "PROMOTION_CREATE"),
    _r("GET", "/promotions/{promotion_id}", "PROMOTION_VIEW"),
    _r("PATCH", "/promotions/{promotion_id}", "PROMOTION_EDIT"),
    _r("POST", "/promotions/{promotion_id}/activate", "PROMOTION_EDIT"),
    _r("POST", "/promotions/{promotion_id}/pause", "PROMOTION_EDIT"),
    _r("POST", "/promotions/{promotion_id}/expire", "PROMOTION_EDIT"),
    # Reports
    _r("GET", "/reports/discounts", "REPORT_VIEW"),
    _r("GET", "/reports/sales-summary", "REPORT_VIEW"),
    # Exports: each needs the permission to see that data AND the permission to download it
    _r("GET", "/exports/products", "PRODUCT_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/inventory", "INVENTORY_VIEW", "INVENTORY_EXPORT"),
    _r("GET", "/exports/inventory-history", "INVENTORY_VIEW", "INVENTORY_EXPORT"),
    _r("GET", "/exports/suppliers", "SUPPLIER_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/purchases", "PURCHASE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/purchase-items", "PURCHASE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/purchases/{purchase_id}", "PURCHASE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/purchase-returns", "PURCHASE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/customers", "CUSTOMER_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/customers/{customer_id}/ledger", "KHATA_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/sales", "SALE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/sale-items", "SALE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/sales/{sale_id}", "SALE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/quick-sales", "SALE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/sales-returns", "SALE_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/promotions", "PROMOTION_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/promotion-usage", "PROMOTION_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/price-history", "PRICE_INTELLIGENCE_USE", "REPORT_EXPORT"),
    _r("GET", "/exports/sales-summary", "REPORT_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/discount-report", "REPORT_VIEW", "REPORT_EXPORT"),
    # AI: using it, and (separately) confirming what it proposes
    _r("GET", "/ai/status", "AI_USE"),
    _r("POST", "/ai/ask", "AI_USE"),
    _r("POST", "/ai/tools/{name}", "AI_USE"),
    _r("POST", "/ai/documents/extract", "AI_USE"),
    _r("POST", "/ai/documents/match", "AI_USE"),
    _r("GET", "/ai/actions", "AI_USE"),
    _r("POST", "/ai/actions", "AI_USE"),
    _r("GET", "/ai/actions/{action_id}", "AI_USE"),
    _r("PATCH", "/ai/actions/{action_id}", "AI_USE"),
    _r("POST", "/ai/actions/{action_id}/cancel", "AI_USE"),
    _r("POST", "/ai/actions/{action_id}/refresh-stock", "AI_USE"),
    _r("POST", "/ai/actions/{action_id}/confirm", "AI_USE", "AI_ACTION_CONFIRM"),
    # Staff and roles
    _r("GET", "/staff", "STAFF_VIEW"),
    _r("GET", "/staff/{member_id}", "STAFF_VIEW"),
    _r("POST", "/staff/invitations", "STAFF_INVITE"),
    _r("GET", "/staff/invitations", "STAFF_VIEW"),
    _r("POST", "/staff/invitations/{invitation_id}/revoke", "STAFF_INVITE"),
    _r("PATCH", "/staff/{member_id}", "STAFF_EDIT"),
    _r("POST", "/staff/{member_id}/suspend", "STAFF_SUSPEND"),
    _r("POST", "/staff/{member_id}/reactivate", "STAFF_SUSPEND"),
    _r("POST", "/staff/{member_id}/remove", "STAFF_SUSPEND"),
    _r("GET", "/roles", "STAFF_VIEW"),
    _r("GET", "/roles/permissions", "STAFF_VIEW"),
    _r("GET", "/roles/{role_id}", "STAFF_VIEW"),
    _r("POST", "/roles", "ROLE_MANAGE"),
    _r("PATCH", "/roles/{role_id}", "ROLE_MANAGE"),
    _r("POST", "/roles/{role_id}/deactivate", "ROLE_MANAGE"),
    _r("POST", "/roles/{role_id}/reactivate", "ROLE_MANAGE"),
    # Inventory intelligence, supplier and customer analytics, business health, the advanced dashboard: all read-only,
    # all gated the same way the existing overview is (REPORT_VIEW), reusing that one permission rather than adding a
    # narrow one per screen.
    _r("GET", "/intelligence/inventory-health", "REPORT_VIEW"),
    _r("GET", "/intelligence/fast-moving", "REPORT_VIEW"),
    _r("GET", "/intelligence/slow-moving", "REPORT_VIEW"),
    _r("GET", "/intelligence/dead-stock", "REPORT_VIEW"),
    _r("GET", "/intelligence/stock-aging", "REPORT_VIEW"),
    _r("GET", "/intelligence/reorder-recommendations", "REPORT_VIEW"),
    _r("GET", "/intelligence/purchase-suggestions", "REPORT_VIEW"),
    _r("POST", "/intelligence/purchase-suggestions/draft", "PURCHASE_CREATE"),
    _r("GET", "/intelligence/suppliers/{supplier_id}", "REPORT_VIEW"),
    _r("GET", "/intelligence/suppliers/{supplier_id}/price-history", "REPORT_VIEW"),
    _r("GET", "/intelligence/customers", "REPORT_VIEW"),
    _r("GET", "/intelligence/customers/{customer_id}", "REPORT_VIEW"),
    _r("GET", "/intelligence/business-health", "REPORT_VIEW"),
    _r("GET", "/intelligence/dashboard", "REPORT_VIEW"),
    # Exports of the above
    _r("GET", "/exports/reorder-recommendations", "REPORT_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/stock-aging", "REPORT_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/supplier-analytics", "REPORT_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/customer-analytics", "REPORT_VIEW", "REPORT_EXPORT"),
    # Stock counting
    _r("GET", "/stock-counts", "STOCK_COUNT_VIEW"),
    _r("POST", "/stock-counts", "STOCK_COUNT_CREATE"),
    _r("GET", "/stock-counts/{count_id}", "STOCK_COUNT_VIEW"),
    _r("POST", "/stock-counts/{count_id}/start-counting", "STOCK_COUNT_CREATE"),
    _r("PUT", "/stock-counts/{count_id}/counts", "STOCK_COUNT_CREATE"),
    _r("POST", "/stock-counts/{count_id}/submit-for-review", "STOCK_COUNT_REVIEW"),
    _r("POST", "/stock-counts/{count_id}/approve", "STOCK_COUNT_APPROVE"),
    _r("POST", "/stock-counts/{count_id}/post", "STOCK_COUNT_POST"),
    _r("POST", "/stock-counts/{count_id}/cancel", "STOCK_COUNT_CREATE"),
    _r("GET", "/exports/stock-counts/{count_id}", "STOCK_COUNT_VIEW", "REPORT_EXPORT"),
    # A generic approval queue (today: only large stock-count variances land in it)
    _r("GET", "/approvals", "STOCK_COUNT_APPROVE"),
    _r("GET", "/approvals/{request_id}", "STOCK_COUNT_APPROVE"),
    _r("POST", "/approvals/{request_id}/decide", "STOCK_COUNT_APPROVE"),
    # Tasks
    _r("GET", "/tasks", "TASK_VIEW"),
    _r("POST", "/tasks", "TASK_CREATE"),
    _r("GET", "/tasks/{task_id}", "TASK_VIEW"),
    _r("PATCH", "/tasks/{task_id}", "TASK_CREATE"),
    _r("POST", "/tasks/{task_id}/assign", "TASK_ASSIGN"),
    _r("POST", "/tasks/{task_id}/complete", "TASK_COMPLETE"),
    _r("POST", "/tasks/{task_id}/reopen", "TASK_COMPLETE"),
    _r("POST", "/tasks/{task_id}/cancel", "TASK_CANCEL"),
    _r("POST", "/tasks/{task_id}/comments", "TASK_CREATE"),
    _r("GET", "/tasks/{task_id}/comments", "TASK_VIEW"),
    # Scheduled reports
    _r("GET", "/scheduled-reports", "REPORT_VIEW"),
    _r("POST", "/scheduled-reports", "SCHEDULED_REPORT_MANAGE"),
    _r("GET", "/scheduled-reports/{report_id}", "REPORT_VIEW"),
    _r("PATCH", "/scheduled-reports/{report_id}", "SCHEDULED_REPORT_MANAGE"),
    _r("POST", "/scheduled-reports/{report_id}/deactivate", "SCHEDULED_REPORT_MANAGE"),
    _r("POST", "/scheduled-reports/{report_id}/reactivate", "SCHEDULED_REPORT_MANAGE"),
    _r("POST", "/scheduled-reports/{report_id}/run-now", "SCHEDULED_REPORT_MANAGE"),
    # CRM: customer profile, notes, timeline, segments, groups
    _r("GET", "/crm/customers/{customer_id}/profile", "CRM_VIEW"),
    _r("GET", "/crm/customers/{customer_id}/timeline", "CRM_VIEW"),
    _r("PATCH", "/crm/customers/{customer_id}/classification", "CRM_MANAGE"),
    _r("GET", "/crm/approval-settings", "CRM_VIEW"),
    _r("PUT", "/crm/approval-settings", "CAMPAIGN_MANAGE", "LOYALTY_MANAGE"),
    _r("GET", "/crm/customers/{customer_id}/notes", "CRM_VIEW"),
    _r("POST", "/crm/customers/{customer_id}/notes", "CRM_MANAGE"),
    _r("GET", "/crm/segments", "CRM_VIEW"),
    _r("GET", "/crm/groups", "CRM_VIEW"),
    _r("POST", "/crm/groups/manual", "CRM_SEGMENT_MANAGE"),
    _r("POST", "/crm/groups/rule-based", "CRM_SEGMENT_MANAGE"),
    _r("GET", "/crm/groups/{group_id}", "CRM_VIEW"),
    _r("GET", "/crm/groups/{group_id}/members", "CRM_VIEW"),
    _r("PUT", "/crm/groups/{group_id}/members", "CRM_SEGMENT_MANAGE"),
    _r("POST", "/crm/groups/{group_id}/recalculate", "CRM_SEGMENT_MANAGE"),
    # CRM dashboard and retention analytics
    _r("GET", "/crm/dashboard", "CRM_ANALYTICS_VIEW"),
    _r("GET", "/crm/retention", "CRM_ANALYTICS_VIEW"),
    _r("GET", "/crm/reactivation-candidates", "CRM_ANALYTICS_VIEW"),
    _r("GET", "/crm/purchase-patterns", "CRM_ANALYTICS_VIEW"),
    _r("GET", "/crm/reactivation/preview", "CRM_ANALYTICS_VIEW"),
    _r("POST", "/crm/reactivation/draft", "CAMPAIGN_MANAGE"),
    # Loyalty
    _r("GET", "/loyalty/program", "LOYALTY_VIEW"),
    _r("PUT", "/loyalty/program", "LOYALTY_MANAGE"),
    _r("GET", "/loyalty/summary", "LOYALTY_VIEW"),
    _r("GET", "/loyalty/customers/{customer_id}/ledger", "LOYALTY_VIEW"),
    _r("POST", "/loyalty/customers/{customer_id}/redeem", "LOYALTY_MANAGE"),
    _r("POST", "/loyalty/customers/{customer_id}/adjust", "LOYALTY_MANAGE"),
    _r("POST", "/loyalty/expire", "LOYALTY_MANAGE"),
    # Campaigns
    _r("GET", "/campaigns", "CAMPAIGN_VIEW"),
    _r("POST", "/campaigns", "CAMPAIGN_MANAGE"),
    _r("GET", "/campaigns/{campaign_id}", "CAMPAIGN_VIEW"),
    _r("PATCH", "/campaigns/{campaign_id}", "CAMPAIGN_MANAGE"),
    _r("GET", "/campaigns/{campaign_id}/audience", "CAMPAIGN_VIEW"),
    _r("POST", "/campaigns/{campaign_id}/schedule", "CAMPAIGN_MANAGE"),
    _r("POST", "/campaigns/{campaign_id}/launch", "CAMPAIGN_LAUNCH"),
    _r("POST", "/campaigns/{campaign_id}/pause", "CAMPAIGN_MANAGE"),
    _r("POST", "/campaigns/{campaign_id}/resume", "CAMPAIGN_MANAGE"),
    _r("POST", "/campaigns/{campaign_id}/cancel", "CAMPAIGN_MANAGE"),
    _r("GET", "/campaigns/{campaign_id}/sends", "CAMPAIGN_VIEW"),
    # Marketing automation
    _r("GET", "/automation-rules", "AUTOMATION_MANAGE"),
    _r("POST", "/automation-rules", "AUTOMATION_MANAGE"),
    _r("GET", "/automation-rules/{rule_id}", "AUTOMATION_MANAGE"),
    _r("POST", "/automation-rules/{rule_id}/activate", "AUTOMATION_MANAGE"),
    _r("POST", "/automation-rules/{rule_id}/deactivate", "AUTOMATION_MANAGE"),
    _r("POST", "/automation-rules/{rule_id}/run", "AUTOMATION_MANAGE"),
    # Referrals
    _r("GET", "/referrals/program", "REFERRAL_VIEW"),
    _r("PUT", "/referrals/program", "REFERRAL_MANAGE"),
    _r("GET", "/referrals/customers/{customer_id}/code", "REFERRAL_VIEW"),
    _r("POST", "/referrals/register", "REFERRAL_MANAGE"),
    _r("GET", "/referrals/events", "REFERRAL_VIEW"),
    # Exports (Phase 14 datasets)
    _r("GET", "/exports/customer-groups/{group_id}", "CRM_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/loyalty-ledger/{customer_id}", "LOYALTY_VIEW", "REPORT_EXPORT"),
    _r("GET", "/exports/campaigns/{campaign_id}", "CAMPAIGN_VIEW", "REPORT_EXPORT"),
)

# Routes that are deliberately outside the rules: they do not act inside a shop. (health, admin console with its own
# token, the sign-in family, metrics.) `tests/test_rbac_routes.py` checks this list is exactly what is left over.
UNGUARDED_PREFIXES = ("/health", "/api/v1/admin", "/api/v1/auth", "/docs", "/openapi.json", "/metrics")

_PARAM = re.compile(r"\{[^}]+\}")


def _matcher(rule: Rule) -> re.Pattern[str]:
    return re.compile(
        "^" + _PARAM.sub("[^/]+", re.escape(rule.template).replace(r"\{", "{").replace(r"\}", "}")) + "$"
    )


def _specificity(rule: Rule) -> tuple[int, int]:
    return (
        len(_PARAM.findall(rule.template)),
        -len(rule.template),
    )  # literal routes win over parameter routes


_COMPILED = [(r, _matcher(r)) for r in sorted(ROUTE_RULES, key=_specificity)]


def rule_for(method: str, path: str) -> Rule | None:
    """The rule for a request (`path` without the /api/v1 prefix), or None when the route has none."""
    for rule, pattern in _COMPILED:
        if rule.method == method.upper() and pattern.match(path):
            return rule
    return None
