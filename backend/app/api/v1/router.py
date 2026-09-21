"""Aggregates all versioned business routers under `/api/v1`.

Each module adds its router here. Routers stay thin: they parse requests, open the transaction for
writes, call `app.services`, and shape the response. Business rules never live here.
"""

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    customers,
    exports,
    image_intelligence,
    inventory,
    price_intelligence,
    products,
    promotions,
    purchases,
    quick_sales,
    reference,
    reports,
    returns,
    sales,
    subscription,
    suppliers,
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
