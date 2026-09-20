from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_versioned_api_is_mounted_under_api_v1():
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/products" in paths and "/api/v1/inventory" in paths
    assert "/health" in paths  # operational routes stay outside the versioned prefix


def test_unknown_routes_are_404():
    assert client.get("/api/v1/no-such-thing").status_code == 404
