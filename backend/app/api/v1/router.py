"""Aggregates all versioned business routers under `/api/v1`.

Each module adds its router here. Routers stay thin: they parse requests, open the transaction for
writes, call `app.services`, and shape the response. Business rules never live here.
"""

from fastapi import APIRouter

from app.api.v1 import (
    account,
    admin,
    ai,
    approvals,
    auth,
    automation,
    campaigns,
    crm,
    crm_dashboard,
    customers,
    exports,
    finance,
    finance_reports,
    image_intelligence,
    intelligence,
    inventory,
    loyalty,
    price_intelligence,
    products,
    promotions,
    purchases,
    quick_sales,
    reference,
    referrals,
    reports,
    returns,
    sales,
    scheduled_reports,
    staff,
    stock_counts,
    subscription,
    suppliers,
    tasks,
)

api_v1_router = APIRouter()
api_v1_router.include_router(reference.router)
api_v1_router.include_router(products.router)
api_v1_router.include_router(suppliers.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(inventory.router)
api_v1_router.include_router(purchases.router)
api_v1_router.include_router(sales.router)
api_v1_router.include_router(returns.sales_returns)
api_v1_router.include_router(returns.purchase_returns)
api_v1_router.include_router(image_intelligence.router)
api_v1_router.include_router(price_intelligence.router)
api_v1_router.include_router(promotions.router)
api_v1_router.include_router(quick_sales.router)
api_v1_router.include_router(subscription.router)
api_v1_router.include_router(reports.router)
api_v1_router.include_router(exports.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(admin.router)
api_v1_router.include_router(account.router)
api_v1_router.include_router(account.notifications)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(staff.staff)
api_v1_router.include_router(staff.roles)
api_v1_router.include_router(intelligence.router)
api_v1_router.include_router(stock_counts.router)
api_v1_router.include_router(tasks.router)
api_v1_router.include_router(approvals.router)
api_v1_router.include_router(scheduled_reports.router)
api_v1_router.include_router(crm.router)
api_v1_router.include_router(crm_dashboard.router)
api_v1_router.include_router(loyalty.router)
api_v1_router.include_router(finance.router)
api_v1_router.include_router(finance_reports.router)
api_v1_router.include_router(campaigns.router)
api_v1_router.include_router(automation.router)
api_v1_router.include_router(referrals.router)
