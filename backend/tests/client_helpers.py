"""A test client that holds exactly the given permissions (the same idea as a custom role)."""

from fastapi.testclient import TestClient

from app.api.deps import get_request_context
from app.core.context import RequestContext
from app.models.enums import UserRole


def client_with(make_client, tenant, permissions, user_id=None) -> TestClient:
    client: TestClient = make_client(tenant)
    ctx = RequestContext(
        shop_id=tenant.shop.id,
        user_id=user_id or tenant.user.id,
        role=UserRole.STAFF,
        permissions=frozenset(permissions),
    )
    client.app.dependency_overrides[get_request_context] = lambda: ctx
    return client
