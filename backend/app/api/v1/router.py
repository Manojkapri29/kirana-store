"""Aggregates all versioned business routers under `/api/v1`.

Each module adds its router here. Routers stay thin: they parse requests, open the transaction for
writes, call `app.services`, and shape the response. Business rules never live here.
"""

from fastapi import APIRouter

from app.api.v1 import customers, exports, inventory, products, purchases, reference, suppliers

api_v1_router = APIRouter()
api_v1_router.include_router(reference.router)
api_v1_router.include_router(products.router)
api_v1_router.include_router(suppliers.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(inventory.router)
api_v1_router.include_router(purchases.router)
api_v1_router.include_router(exports.router)
