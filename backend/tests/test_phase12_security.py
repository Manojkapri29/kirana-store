"""The Phase 12 security sweep, run against the real sign-in (no overrides): audit trail, uploads, error and secret leakage, IDOR."""

import base64
import logging

import pytest
from sqlalchemy import text

from tests import factories
from tests.factories import PASSWORD
from tests.test_image_intelligence import png
from tests.test_purchases_api import make_product, make_supplier

API = "/api/v1"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


@pytest.fixture
def owner(sign_in, owner_login):
    return sign_in(owner_login)


@pytest.fixture
def owner_b(sign_in, tenant_b, session_factory):
    with session_factory() as s, s.begin():
        factories.make_login(s, tenant_b.shop, "OWNER", "b-owner@test.local")
    return sign_in("b-owner@test.local")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class TestAuditTrail:
    def test_every_staff_and_security_event_is_written_with_who_what_whom_and_the_request_id(
        self, owner, sign_in, staff_of, tenant_a, session_factory, real_client
    ):
        owner.get(f"{API}/exports/customers")
        role = next(r["id"] for r in owner.get(f"{API}/roles").json()["items"] if r["code"] == "CASHIER")
        accountant = next(
            r["id"] for r in owner.get(f"{API}/roles").json()["items"] if r["code"] == "ACCOUNTANT"
        )
        token = (
            owner.post(f"{API}/staff/invitations", json={"email": "n@example.com", "role_id": role})
            .json()["link"]
            .split("#token=")[1]
        )
        revoked = owner.post(
            f"{API}/staff/invitations", json={"email": "r@example.com", "role_id": role}
        ).json()["invitation"]["id"]
        owner.post(f"{API}/staff/invitations/{revoked}/revoke")
        real_client().post(
            f"{API}/auth/invitations/accept",
            json={"token": token, "password": "a long passphrase 55", "full_name": "N"},
        )
        member = staff_of(tenant_a, "CASHIER", "c@test.local")
        member_id = next(m["id"] for m in owner.get(f"{API}/staff").json()["items"] if m["email"] == member)
        owner.patch(f"{API}/staff/{member_id}", json={"role_id": accountant})
        owner.post(f"{API}/staff/{member_id}/suspend")
        owner.post(f"{API}/staff/{member_id}/reactivate")
        owner.post(f"{API}/staff/{member_id}/remove")
        made = owner.post(f"{API}/roles", json={"name": "Lead", "permissions": ["PRODUCT_VIEW"]}).json()["id"]
        owner.patch(f"{API}/roles/{made}", json={"permissions": ["PRODUCT_VIEW", "SALE_VIEW"]})
        owner.post(f"{API}/roles/{made}/deactivate")
        owner.post(
            f"{API}/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "another long passphrase 9"},
        )
        owner.post(f"{API}/auth/logout")
        with session_factory() as s:
            rows = s.execute(
                text(
                    "SELECT action, user_id, entity_type, entity_id, request_id, before_json, after_json FROM audit_log"
                )
            ).all()
        actions = {r[0] for r in rows}
        expected = {
            "login",
            "logout",
            "invitation_created",
            "invitation_revoked",
            "invitation_accepted",
            "role_changed",
            "staff_suspended",
            "staff_reactivated",
            "membership_removed",
            "role_created",
            "permission_changed",
            "role_deactivated",
            "password_changed",
            "export",
        }
        assert expected <= actions, expected - actions
        by_action = {r[0]: r for r in rows}
        assert (
            by_action["staff_suspended"][2] == "user"
            and by_action["staff_suspended"][3] == member_id
            and by_action["staff_suspended"][1] is not None
        )
        assert all(r[4] and r[4].startswith("req_") for r in rows if r[0] in expected)
        everything = " ".join(str(r) for r in rows)
        for secret in (token, "a long passphrase 55", PASSWORD, "another long passphrase 9", "argon2"):
            assert secret not in everything

    def test_a_shops_audit_log_never_shows_another_shops_events(self, owner, owner_b):
        owner_b.post(f"{API}/roles", json={"name": "Only B", "permissions": ["PRODUCT_VIEW"]})
        mine = owner.get(f"{API}/audit-log", params={"limit": 100}).json()
        assert "Only B" not in str(mine) and all(e["action"] != "role_created" for e in mine["items"])

    def test_only_those_allowed_read_the_audit_log(self, sign_in, staff_of, tenant_a):
        assert sign_in(staff_of(tenant_a, "CASHIER")).get(f"{API}/audit-log").status_code == 403
        assert sign_in(staff_of(tenant_a, "ACCOUNTANT")).get(f"{API}/audit-log").status_code == 200


class TestUploads:
    def analyze(self, client, data: bytes, **extra):
        return client.post(f"{API}/image-intelligence/analyze", json={"image_base64": b64(data), **extra})

    def test_a_person_who_may_not_add_products_cannot_upload(self, sign_in, staff_of, tenant_a):
        assert self.analyze(sign_in(staff_of(tenant_a, "ACCOUNTANT")), png()).status_code == 403

    def test_a_disguised_program_and_a_wrong_type_are_refused_safely(self, owner):
        program = self.analyze(owner, b"MZ" + b"\x00" * 300, content_type="image/png")
        assert program.status_code == 422 and "Traceback" not in program.text
        text_file = self.analyze(owner, b"<script>alert(1)</script>", content_type="image/png")
        assert text_file.status_code == 422 and "<script>" not in text_file.text
        assert self.analyze(owner, png(), content_type="application/x-msdownload").status_code == 422

    def test_a_file_name_or_path_in_the_request_is_not_accepted_at_all(self, owner):
        refused = owner.post(
            f"{API}/image-intelligence/analyze",
            json={"image_base64": b64(png()), "filename": "../../etc/passwd"},
        )
        assert refused.status_code == 422  # unknown fields are refused: nothing names a path

    def test_an_oversized_or_absurd_image_is_refused(self, owner):
        assert self.analyze(owner, png(pad=6_000_000)).status_code in (413, 422)
        assert self.analyze(owner, png(width=40000, height=40000)).status_code == 422


class TestLeakage:
    def test_an_unexpected_failure_shows_a_reference_and_nothing_else(self, owner, monkeypatch, caplog):
        from app.services import catalog_service

        def boom(*a, **k):
            raise RuntimeError("secret internal detail /Users/x/db.py password=hunter2 sqlalchemy")

        monkeypatch.setattr(catalog_service, "list_categories", boom, raising=False)
        monkeypatch.setattr("app.services.product_service.list_products", boom)
        caplog.set_level(logging.ERROR)
        from fastapi.testclient import TestClient

        quiet = TestClient(
            owner.app, raise_server_exceptions=False
        )  # answer like a real server instead of re-raising
        quiet.cookies.update(owner.cookies)
        response = quiet.get(f"{API}/products")
        assert response.status_code == 500 and response.json()["reference_id"].startswith("ERR-")
        body = response.text
        for leak in ("secret internal", "/Users", "hunter2", "sqlalchemy", "Traceback", "RuntimeError"):
            assert leak not in body

    def test_denials_and_sign_in_answers_carry_no_internal_detail(self, real_client, owner):
        for response in (
            real_client().get(f"{API}/products"),
            owner.get(f"{API}/purchases/999999"),
            owner.get(f"{API}/no/such/route"),
            real_client().post(
                f"{API}/auth/login", json={"email": "x@example.com", "password": "nope nope nope"}
            ),
        ):
            assert response.status_code in (401, 403, 404, 422)
            for leak in ("Traceback", "sqlite", 'File "', "argon2", "/Users/"):
                assert leak not in response.text

    def test_no_secret_appears_in_any_response_of_the_auth_and_staff_api(
        self, owner, real_client, owner_login, staff_of, tenant_a
    ):
        client = real_client()
        signed = client.post(f"{API}/auth/login", json={"email": owner_login, "password": PASSWORD})
        staff_of(tenant_a, "CASHIER", "spy@test.local")
        pages = [
            signed.text,
            owner.get(f"{API}/auth/me").text,
            owner.get(f"{API}/staff").text,
            owner.get(f"{API}/roles").text,
        ]
        for page in pages:
            for secret in ("password_hash", "argon2", "token_hash", "csrf_hash", PASSWORD):
                assert secret not in page

    def test_the_session_cookie_is_the_only_place_the_session_token_appears(self, real_client, owner_login):
        client = real_client()
        response = client.post(f"{API}/auth/login", json={"email": owner_login, "password": PASSWORD})
        token = client.cookies.get("kirana_session")
        assert token not in response.text and all(
            token not in v for k, v in response.headers.items() if k != "set-cookie"
        )


class TestCrossShopIdSweep:
    def test_another_shops_records_are_never_reachable_by_id_or_by_a_forged_shop_id(
        self, owner, owner_b, tenant_b, units
    ):
        product = make_product(owner_b, tenant_b, units, "SECRET")
        supplier = make_supplier(owner_b, "Their Supplier")
        customer = owner_b.post(f"{API}/customers", json={"name": "Their Customer"}).json()["customer"]
        for path in (
            f"/products/{product['id']}",
            f"/inventory/products/{product['id']}",
            f"/suppliers/{supplier['id']}",
            f"/customers/{customer['id']}",
            f"/customers/{customer['id']}/ledger",
            f"/customers/{customer['id']}/balance",
            f"/exports/customers/{customer['id']}/ledger",
            f"/image-intelligence/products/{product['id']}/image",
        ):
            for extra in ("", f"?shop_id={tenant_b.shop.id}"):
                assert owner.get(API + path + extra).status_code == 404, path
        for method, path in (
            ("PATCH", f"/products/{product['id']}"),
            ("POST", f"/products/{product['id']}/deactivate"),
            ("POST", f"/customers/{customer['id']}/payments"),
            ("PATCH", f"/suppliers/{supplier['id']}"),
            ("POST", f"/customers/{customer['id']}/deactivate"),
        ):
            assert owner.request(
                method, API + path, json={"amount": "1", "payment_method": "CASH"}
            ).status_code in (404, 422), path
        # nothing of theirs is in ours
        assert owner.get(f"{API}/products", params={"q": "SECRET"}).json()["total"] == 0
        assert owner.get(f"{API}/customers", params={"q": "Their"}).json()["total"] == 0
        assert owner_b.get(f"{API}/customers/{customer['id']}").status_code == 200  # and theirs is untouched
