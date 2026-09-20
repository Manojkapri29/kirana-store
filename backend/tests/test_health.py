from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_versioned_api_router_is_mounted_but_empty():
    # No business endpoints exist in Phase 1, so the versioned prefix has no routes yet.
    response = client.get("/api/v1/products")

    assert response.status_code == 404
