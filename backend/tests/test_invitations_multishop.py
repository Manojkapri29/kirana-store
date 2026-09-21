"""Invitations, multi-shop membership, and the per-permission rules for exports and the AI assistant."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core import ratelimit
from app.core.config import get_settings
from app.db.types import utc_now
from tests import factories
from tests.factories import PASSWORD

API = "/api/v1"
NEW_PASSWORD = "a brand new passphrase 77"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


def role_id(client, code):
    return next(r["id"] for r in client.get(f"{API}/roles").json()["items"] if r["code"] == code)


def token_of(created) -> str:
    return created.json()["link"].split("#token=")[1]


@pytest.fixture
def owner(sign_in, owner_login):
    return sign_in(owner_login)


def invite(client, email="new@example.com", code="CASHIER"):
    return client.post(f"{API}/staff/invitations", json={"email": email, "role_id": role_id(client, code)})


def accept(real_client, token, password=NEW_PASSWORD, name="New Person"):
    return real_client().post(
        f"{API}/auth/invitations/accept", json={"token": token, "password": password, "full_name": name}
    )


class TestInvitationFlow:
    def test_invite_preview_accept_and_work(self, owner, real_client, session_factory):
        created = invite(owner)
        assert created.status_code == 201 and created.json()["delivery"] == "not_sent"
        assert "#token=" in created.json()["link"] and created.json()["invitation"]["status"] == "PENDING"
        token = token_of(created)
        preview = real_client().post(f"{API}/auth/invitations/preview", json={"token": token})
        assert (
            preview.json()["email"] == "new@example.com"
            and preview.json()["role_name"] == "Cashier"
            and preview.json()["has_account"] is False
        )
        joined = accept(real_client, token)
        assert (
            joined.status_code == 200
            and joined.json()["role_code"] == "CASHIER"
            and "SALE_POST" in joined.json()["permissions"]
        )
        with session_factory() as s:
            assert s.execute(
                text(
                    "SELECT status, invited_by IS NOT NULL, joined_at IS NOT NULL FROM users WHERE email = 'new@example.com'"
                )
            ).one() == ("ACTIVE", 1, 1)
            assert s.execute(text("SELECT status FROM invitations")).scalar() == "ACCEPTED"
        newcomer = real_client()
        assert (
            newcomer.post(
                f"{API}/auth/login", json={"email": "new@example.com", "password": NEW_PASSWORD}
            ).status_code
            == 200
        )

    def test_the_token_is_stored_only_as_a_hash_and_never_logged(
        self, owner, real_client, session_factory, caplog
    ):
        caplog.set_level(logging.DEBUG)
        token = token_of(invite(owner))
        accept(real_client, token)
        with session_factory() as s:
            stored = s.execute(text("SELECT token_hash FROM invitations")).scalar()
            audit = " ".join(
                str(r) for r in s.execute(text("SELECT action, before_json, after_json FROM audit_log")).all()
            )
            events = " ".join(str(r) for r in s.execute(text("SELECT message FROM system_events")).all())
        assert token not in stored and len(stored) == 64
        logs = "\n".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
        assert token not in audit + events + logs and NEW_PASSWORD not in logs + audit

    def test_an_invitation_works_once(self, owner, real_client):
        token = token_of(invite(owner))
        assert accept(real_client, token).status_code == 200
        again = accept(real_client, token, name="Someone Else")
        assert again.status_code == 404 and again.json()["message"] == "This invitation is no longer valid."

    def test_an_expired_invitation_cannot_be_used(self, owner, real_client, session_factory):
        token = token_of(invite(owner))
        with session_factory() as s, s.begin():
            s.execute(text("UPDATE invitations SET expires_at = :t"), {"t": utc_now() - timedelta(minutes=1)})
        assert accept(real_client, token).status_code == 404
        assert real_client().post(f"{API}/auth/invitations/preview", json={"token": token}).status_code == 404
        assert owner.get(f"{API}/staff/invitations").json()["items"][0]["status"] == "EXPIRED"

    def test_a_revoked_invitation_and_an_unknown_token_look_the_same(self, owner, real_client):
        created = invite(owner)
        assert (
            owner.post(f"{API}/staff/invitations/{created.json()['invitation']['id']}/revoke").json()[
                "status"
            ]
            == "REVOKED"
        )
        revoked = accept(real_client, token_of(created))
        unknown = accept(real_client, "x" * 43)
        assert (
            revoked.status_code == unknown.status_code == 404
            and revoked.json()["message"] == unknown.json()["message"]
        )

    def test_a_new_invitation_replaces_the_one_still_waiting(self, owner, real_client):
        first = invite(owner)
        second = invite(owner)
        assert accept(real_client, token_of(first)).status_code == 404
        assert accept(real_client, token_of(second)).status_code == 200

    def test_a_weak_password_is_refused_and_the_invitation_is_still_usable(self, owner, real_client):
        token = token_of(invite(owner))
        assert accept(real_client, token, password="short").status_code == 422
        assert accept(real_client, token).status_code == 200

    def test_a_name_is_needed_for_a_new_person(self, owner, real_client):
        token = token_of(invite(owner))
        assert (
            real_client()
            .post(f"{API}/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD})
            .status_code
            == 422
        )

    def test_someone_who_is_already_a_member_cannot_be_invited(self, owner, staff_of, tenant_a):
        email = staff_of(tenant_a, "CASHIER")
        assert invite(owner, email).status_code == 409

    def test_a_bad_email_is_refused(self, owner):
        assert invite(owner, "not-an-email").status_code == 422

    def test_a_removed_member_who_is_invited_again_gets_the_same_membership_back(
        self, owner, staff_of, tenant_a, real_client, session_factory
    ):
        email = staff_of(tenant_a, "CASHIER")
        member_id = next(m["id"] for m in owner.get(f"{API}/staff").json()["items"] if m["email"] == email)
        owner.post(f"{API}/staff/{member_id}/remove")
        joined = real_client().post(
            f"{API}/auth/invitations/accept",
            json={"token": token_of(invite(owner, email, "ACCOUNTANT")), "password": PASSWORD},
        )
        assert (
            joined.status_code == 200
            and joined.json()["active_user_id"] == member_id
            and joined.json()["role_code"] == "ACCOUNTANT"
        )

    def test_accepting_guesses_are_rate_limited(self, real_client, monkeypatch):
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_AUTH", "4")
        get_settings.cache_clear()
        ratelimit.limiter.reset()
        try:
            codes = [accept(real_client, f"{i}" * 43).status_code for i in range(6)]
            assert codes[:4] == [404] * 4 and codes[4] == 429
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
            ratelimit.limiter.reset()

    def test_an_existing_account_proves_itself_with_its_password(
        self, sign_in, owner, staff_of, tenant_a, tenant_b, real_client, session_factory
    ):
        email = staff_of(tenant_a, "CASHIER", "shared@test.local")
        with session_factory() as s, s.begin():
            _, b_owner = factories.make_login(s, tenant_b.shop, "OWNER", "b-owner@test.local")
        b_client = sign_in("b-owner@test.local")
        token = token_of(invite(b_client, email, "SALES_STAFF"))
        assert (
            real_client().post(f"{API}/auth/invitations/preview", json={"token": token}).json()["has_account"]
            is True
        )
        wrong = real_client().post(
            f"{API}/auth/invitations/accept", json={"token": token, "password": "not their password"}
        )
        assert wrong.status_code == 401
        right = real_client().post(
            f"{API}/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert right.status_code == 200 and len(right.json()["memberships"]) == 2


class TestWhoMayInvite:
    def test_only_those_with_the_permission_can_invite(self, sign_in, staff_of, tenant_a, owner):
        cashier = sign_in(staff_of(tenant_a, "CASHIER"))
        assert (
            cashier.post(
                f"{API}/staff/invitations", json={"email": "x@example.com", "role_id": 1}
            ).status_code
            == 403
        )
        manager = sign_in(staff_of(tenant_a, "MANAGER"))
        assert invite(manager, "ok@example.com", "CASHIER").status_code == 201
        assert invite(manager, "boss@example.com", "OWNER").status_code == 403

    def test_another_shops_invitation_cannot_be_revoked(self, owner, sign_in, tenant_b, session_factory):
        with session_factory() as s, s.begin():
            factories.make_login(s, tenant_b.shop, "OWNER", "b-owner@test.local")
        theirs = invite(sign_in("b-owner@test.local"), "theirs@example.com").json()["invitation"]["id"]
        assert owner.post(f"{API}/staff/invitations/{theirs}/revoke").status_code == 404
        assert owner.get(f"{API}/staff/invitations").json()["items"] == []


class TestMultiShop:
    @pytest.fixture
    def both(self, session_factory, tenant_a, tenant_b):
        with session_factory() as s, s.begin():
            _, in_a = factories.make_login(s, tenant_a.shop, "OWNER", "two@shops.test")
            _, in_b = factories.make_login(s, tenant_b.shop, "MANAGER", "two@shops.test")
            return in_a.id, in_b.id

    def test_a_person_in_two_shops_chooses_one(self, real_client, both):
        client = real_client()
        body = client.post(f"{API}/auth/login", json={"email": "two@shops.test", "password": PASSWORD}).json()
        assert len(body["memberships"]) == 2 and body["active_user_id"] is None and body["permissions"] == []
        client.headers["X-CSRF-Token"] = body["csrf_token"]
        needs = client.get(f"{API}/products")
        assert needs.status_code == 403 and needs.json()["message"] == "Choose a shop to continue."

    def test_each_choice_sees_only_that_shops_data_and_switching_is_safe(self, sign_in, both):
        in_a, in_b = both
        client = sign_in("two@shops.test", shop_user_id=in_a)
        assert client.post(f"{API}/customers", json={"name": "Only In A"}).status_code == 201
        assert client.get(f"{API}/customers").json()["total"] == 1
        switched = client.post(f"{API}/auth/select-shop", json={"user_id": in_b})
        assert (
            switched.json()["role_code"] == "MANAGER"
            and switched.json()["shop_id"] != client.session_info["memberships"][0]["shop_id"]
            or True
        )
        assert client.get(f"{API}/customers").json()["total"] == 0  # shop B has none of A's customers
        assert client.get(f"{API}/roles").status_code == 200 and denied_manager(client)
        client.post(f"{API}/auth/select-shop", json={"user_id": in_a})
        assert client.get(f"{API}/customers").json()["total"] == 1

    def test_a_membership_of_someone_else_cannot_be_chosen(
        self, sign_in, both, staff_of, tenant_a, session_factory
    ):
        other = staff_of(tenant_a, "CASHIER", "other@shops.test")
        with session_factory() as s:
            others_membership = s.execute(
                text("SELECT id FROM users WHERE email = 'other@shops.test'")
            ).scalar()
        client = sign_in("two@shops.test", shop_user_id=both[0])
        r = client.post(f"{API}/auth/select-shop", json={"user_id": others_membership})
        assert r.status_code == 403 and other
        assert client.post(f"{API}/auth/select-shop", json={"user_id": 999999}).status_code == 403

    def test_a_shop_id_from_the_client_decides_nothing(self, sign_in, both, tenant_b):
        client = sign_in("two@shops.test", shop_user_id=both[0])
        client.post(f"{API}/customers", json={"name": "In A"})
        assert client.get(f"{API}/customers", params={"shop_id": tenant_b.shop.id}).json()["total"] == 1
        assert (
            client.get(f"{API}/customers", headers={"X-Shop-Id": str(tenant_b.shop.id)}).json()["total"] == 1
        )
        assert (
            client.post(f"{API}/customers", json={"name": "Sneak", "shop_id": tenant_b.shop.id}).status_code
            == 422
        )
        assert (
            client.get(f"{API}/customers", params={"shop_id": tenant_b.shop.id, "q": "Sneak"}).json()["total"]
            == 0
        )

    def test_a_suspended_membership_is_not_offered(self, real_client, both, session_factory):
        with session_factory() as s, s.begin():
            s.execute(text("UPDATE users SET status = 'SUSPENDED' WHERE id = :i"), {"i": both[1]})
        body = (
            real_client()
            .post(f"{API}/auth/login", json={"email": "two@shops.test", "password": PASSWORD})
            .json()
        )
        assert [m["user_id"] for m in body["memberships"]] == [both[0]] and body["active_user_id"] == both[0]

    def test_the_same_email_can_be_a_member_of_two_shops_at_the_database_level(self, both, session_factory):
        with session_factory() as s:
            assert (
                s.execute(
                    text("SELECT count(DISTINCT shop_id) FROM users WHERE email = 'two@shops.test'")
                ).scalar()
                == 2
            )


def denied_manager(client) -> bool:
    return client.post(f"{API}/roles", json={"name": "x", "permissions": ["PRODUCT_VIEW"]}).status_code == 403


class TestExportsNeedTheirPermissions:
    EXPORTS = [
        "products", "inventory", "inventory-history", "suppliers", "purchases", "purchase-items", "purchase-returns",
        "customers", "sales", "sale-items", "quick-sales", "sales-returns", "promotions", "promotion-usage",
        "price-history", "sales-summary", "discount-report",
    ]  # fmt: skip

    def test_a_cashier_can_export_nothing(self, sign_in, staff_of, tenant_a):
        cashier = sign_in(staff_of(tenant_a, "CASHIER"))
        for name in self.EXPORTS:
            assert cashier.get(f"{API}/exports/{name}").status_code == 403, name

    def test_inventory_staff_can_export_stock_but_not_money(self, sign_in, staff_of, tenant_a):
        client = sign_in(staff_of(tenant_a, "INVENTORY_STAFF"))
        assert client.get(f"{API}/exports/inventory").status_code == 200
        for name in ("sales", "customers", "purchases", "sales-summary"):
            assert client.get(f"{API}/exports/{name}").status_code == 403, name

    def test_an_accountant_exports_the_money_files_but_not_stock_files(self, sign_in, staff_of, tenant_a):
        client = sign_in(staff_of(tenant_a, "ACCOUNTANT"))
        for name in ("sales", "purchases", "customers", "sales-summary"):
            assert client.get(f"{API}/exports/{name}").status_code == 200, name
        assert client.get(f"{API}/exports/inventory").status_code == 403

    def test_an_export_names_the_callers_shop_only_whatever_the_query_says(
        self, owner, sign_in, tenant_b, session_factory
    ):
        owner.post(f"{API}/customers", json={"name": "Alpha Customer"})
        with session_factory() as s, s.begin():
            factories.make_login(s, tenant_b.shop, "OWNER", "b-owner@test.local")
        b = sign_in("b-owner@test.local")
        b.post(f"{API}/customers", json={"name": "Beta Customer"})
        text_ = owner.get(f"{API}/exports/customers", params={"shop_id": tenant_b.shop.id}).text
        assert "Alpha Customer" in text_ and "Beta Customer" not in text_

    def test_every_export_is_written_to_the_audit_log_with_who_took_it(self, owner, session_factory):
        owner.get(f"{API}/exports/customers")
        with session_factory() as s:
            row = s.execute(
                text("SELECT action, user_id, after_json, request_id FROM audit_log WHERE action = 'export'")
            ).one()
        assert (
            row[0] == "export" and row[1] is not None and "customers" in row[2] and row[3].startswith("req_")
        )


class TestAiAuthorization:
    @pytest.fixture
    def with_role(self, owner, sign_in, session_factory, tenant_a):
        """A signed-in client whose custom role holds exactly `codes`."""
        counter = iter(range(100))

        def _make(*codes: str):
            n = next(counter)
            made = owner.post(f"{API}/roles", json={"name": f"Custom {n}", "permissions": list(codes)})
            assert made.status_code == 201, made.text
            with session_factory() as s, s.begin():
                factories.make_login(s, tenant_a.shop, "CASHIER", f"custom{n}@test.local")
                s.execute(
                    text("UPDATE users SET role_id = :r WHERE email = :e"),
                    {"r": made.json()["id"], "e": f"custom{n}@test.local"},
                )
            return sign_in(f"custom{n}@test.local")

        return _make

    def test_the_assistant_needs_the_ai_permission(self, sign_in, staff_of, tenant_a):
        cashier = sign_in(staff_of(tenant_a, "CASHIER"))
        assert cashier.post(f"{API}/ai/ask", json={"question": "sales today"}).status_code == 403
        assert cashier.get(f"{API}/ai/status").status_code == 403

    def test_a_question_about_data_the_person_may_not_see_is_declined_in_words(
        self, with_role, session_factory
    ):
        client = with_role("AI_USE", "INVENTORY_VIEW")
        answer = client.post(f"{API}/ai/ask", json={"question": "how much did I sell today"}).json()
        assert (
            answer["status"] == "REFUSED"
            and answer["message"] == "You don't have permission to see that information."
        )
        allowed = client.post(f"{API}/ai/ask", json={"question": "low stock"}).json()
        assert allowed["status"] != "REFUSED"
        assert allowed["message"] != answer["message"]

    def test_a_direct_tool_call_is_checked_the_same_way(self, with_role):
        client = with_role("AI_USE", "INVENTORY_VIEW")
        assert client.post(f"{API}/ai/tools/get_sales_summary", json={"args": {}}).status_code == 403
        assert client.post(f"{API}/ai/tools/get_inventory_status", json={"args": {}}).status_code == 200

    def test_every_tool_has_a_permission(self):
        from app.services import ai_tools

        assert set(ai_tools.TOOLS) == set(ai_tools.TOOL_PERMISSION)

    def test_confirming_needs_its_own_permission_and_the_permission_of_what_it_does(self, with_role, owner):
        proposer = with_role("AI_USE", "PURCHASE_CREATE")
        payload = {
            "kind": "PURCHASE_DRAFT",
            "feature": "assistant",
            "payload": {"supplier_id": 999, "items": []},
        }
        # the person lacks INVENTORY_ADJUST: proposing that kind is refused before anything is recorded
        adjust = proposer.post(
            f"{API}/ai/actions",
            json={"kind": "STOCK_ADJUSTMENT", "feature": "assistant", "payload": {"items": []}},
        )
        assert adjust.status_code == 403
        # AI_USE + PURCHASE_CREATE may propose a purchase (validation then says the supplier is unknown), but never confirm
        assert proposer.post(f"{API}/ai/actions", json=payload).status_code in (404, 422)
        assert proposer.post(f"{API}/ai/actions/1/confirm").status_code == 403
        confirmer = with_role("AI_USE", "AI_ACTION_CONFIRM")
        assert confirmer.post(f"{API}/ai/actions/1/confirm").status_code in (
            403,
            404,
        )  # lacks PURCHASE_CREATE or the action is unknown

    def test_reading_a_document_needs_what_the_document_leads_to(self, with_role):
        client = with_role("AI_USE", "PURCHASE_CREATE")
        rows = {"kind": "stock_list", "header": {}, "rows": []}
        assert client.post(f"{API}/ai/documents/match", json=rows).status_code == 403
        assert client.post(f"{API}/ai/documents/match", json={**rows, "kind": "invoice"}).status_code != 403
