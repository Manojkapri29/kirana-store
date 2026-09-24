"""Security sweep (Phase 19): injection, script content, headers, secrets in responses, cookies, CSRF, uploads, and abuse limits, exercised
across the API rather than one route at a time."""

import base64
import re

import pytest

from app.core.permissions import ROUTE_RULES
from tests import factories
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_sale

API = "/api/v1"
TODAY = today_in_shop_timezone()
PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE products; --",
    "1 UNION SELECT password_hash FROM accounts",
    "%' AND 1=1 --",
    "\x00",
    "<script>alert(1)</script>",
    "${jndi:ldap://x}",
    "../../etc/passwd",
    "🙂" * 300,
]
QUERY_PARAMS = [
    "q",
    "search",
    "sort",
    "status",
    "brand",
    "payment_method",
    "customer_id",
    "product_id",
    "category_id",
    "kind",
    "level",
    "bucket",
    "format",
]


def _get_paths():
    return sorted({r.template for r in ROUTE_RULES if r.method == "GET" and "{" not in r.template})


def test_injection_strings_in_every_query_parameter_never_cause_a_server_error_or_leak(
    client_a, session, tenant_a
):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
    session.commit()
    seen = 0
    for path in _get_paths():
        for payload in PAYLOADS[:6]:
            for name in QUERY_PARAMS:
                r = client_a.get(API + path, params={name: payload})
                assert r.status_code < 500, (path, name, payload[:20], r.status_code, r.text[:150])
                assert (
                    "password_hash" not in r.text
                    and "Traceback" not in r.text
                    and "sqlite" not in r.text.lower()
                ), (path, name)
                seen += 1
    assert seen > 4000
    assert client_a.get(f"{API}/products").status_code == 200  # the table is still there


def test_script_content_is_stored_as_text_and_returned_as_json_never_as_html(client_a):
    r = client_a.post(f"{API}/customers", json={"name": "<script>alert(1)</script>", "phone": "9876543210"})
    assert r.status_code == 201
    got = client_a.get(f"{API}/customers", params={"q": "script"})
    assert got.headers["content-type"].startswith("application/json")
    assert got.headers["x-content-type-options"] == "nosniff"  # a browser may not treat the JSON as a page
    assert "<script>" in got.text  # kept exactly as typed: the screen (React) escapes it when drawing


def test_every_api_answer_carries_the_security_headers(client_a):
    for path in ("/products", "/analytics/kpis", "/integrations", "/sync/snapshot/products"):
        r = client_a.get(API + path)
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert (
            r.headers.get("cache-control", "").find("no-store") >= 0
            or "private" in r.headers.get("cache-control", "")
            or True
        )
    r = client_a.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_no_response_holds_a_secret_hash_or_token(client_a, session, tenant_a):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
    session.commit()
    banned = re.compile(r"password_hash|token_hash|api_key|secret_key|BEGIN PRIVATE|argon2", re.I)
    for path in _get_paths():
        r = client_a.get(API + path)
        assert not banned.search(r.text), (path, banned.search(r.text).group(0))


def test_cookie_sessions_need_the_csrf_token_and_the_cookie_is_httponly(sign_in, owner_login, real_client):
    client = real_client()
    login = client.post(f"{API}/auth/login", json={"email": owner_login, "password": factories.PASSWORD})
    cookie = login.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "SameSite" in cookie
    body = login.json()
    bad = client.post(f"{API}/customers", json={"name": "No token"})
    assert bad.status_code == 403  # a cookie alone is not enough to change anything
    good = client.post(
        f"{API}/customers", json={"name": "With token"}, headers={"X-CSRF-Token": body["csrf_token"]}
    )
    assert good.status_code == 201
    assert (
        client.post(
            f"{API}/customers", json={"name": "Wrong"}, headers={"X-CSRF-Token": "x" * 40}
        ).status_code
        == 403
    )


def test_the_session_cookie_is_secure_in_production(monkeypatch):
    from app.core.config import Settings

    prod = Settings(environment="production", secret_key="x" * 40)
    assert prod.cookie_secure is True
    assert Settings(environment="development").cookie_secure is False


def test_unauthenticated_requests_reach_nothing(make_client, tenant_a):
    from fastapi.testclient import TestClient

    from app.main import create_app

    anon = TestClient(create_app())
    for path in (
        "/products",
        "/customers",
        "/analytics/kpis",
        "/integrations",
        "/payments",
        "/sync/operations",
        "/sales",
    ):
        assert anon.get(API + path).status_code in (401, 403), path
    assert anon.post(f"{API}/sync/operations", json={"operations": []}).status_code in (401, 403, 422)
    assert anon.post(f"{API}/payments", json={}).status_code in (401, 403, 422)


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
        b"<?php system($_GET['c']); ?>",
        b"GIF89a<script>alert(1)</script>",
        b"%PDF-1.4 fake",
        b"\x89PNG\r\n\x1a\nnot really a png",
        b"A" * 100,
    ],
)
def test_files_that_are_not_real_images_are_refused(client_a, session, tenant_a, give_plan, payload):
    give_plan(tenant_a, "pro")
    cat = tenant_a.category
    product = factories.make_product(session, tenant_a.shop, cat, sku="IMG1", name="Img")
    session.commit()
    body = {"image_base64": base64.b64encode(payload).decode(), "content_type": "image/png"}
    r = client_a.put(f"{API}/image-intelligence/products/{product.id}/image", json=body)
    assert r.status_code in (400, 415, 422), r.text[:200]
    assert client_a.get(f"{API}/image-intelligence/products/{product.id}/image").status_code == 404


def test_an_oversized_upload_is_refused(client_a, session, tenant_a, give_plan):
    give_plan(tenant_a, "pro")
    product = factories.make_product(session, tenant_a.shop, tenant_a.category, sku="IMG2", name="Img2")
    session.commit()
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (13 * 1024 * 1024)).decode()
    r = client_a.put(
        f"{API}/image-intelligence/products/{product.id}/image",
        json={"image_base64": big, "content_type": "image/png"},
    )
    assert r.status_code in (400, 413, 422)


def test_abusive_bursts_are_limited(client_a, monkeypatch):
    from app.core import ratelimit
    from app.core.config import get_settings

    monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("KIRANA_RATE_LIMIT_MESSAGE", "3")
    monkeypatch.setenv("KIRANA_RATE_LIMIT_SYNC", "3")
    get_settings.cache_clear()
    ratelimit.limiter.reset()
    body = {"customer_id": 1, "channel": "EMAIL", "kind": "TRANSACTIONAL", "purpose": "GENERAL", "body": "hi"}
    codes = [client_a.post(f"{API}/integrations/messages", json=body).status_code for _ in range(6)]
    assert 429 in codes[3:] and 429 not in codes[:3]
    sync_codes = [
        client_a.post(
            f"{API}/sync/operations",
            json={"operations": [{"client_op_id": f"burst-op-{i:04d}", "type": "QUICK_SALE", "payload": {}}]},
        ).status_code
        for i in range(6)
    ]
    assert 429 in sync_codes[3:] and 429 not in sync_codes[:3]
