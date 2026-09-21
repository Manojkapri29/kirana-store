"""Roles and permissions: every role against every route, the rules for managing staff, custom roles, and multi-shop."""

import pytest
from sqlalchemy import text

from app.core import permissions as perms
from app.services.authorization_service import DENIED
from tests import factories

ROLE_CODES = ["OWNER", "MANAGER", "CASHIER", "INVENTORY_STAFF", "SALES_STAFF", "ACCOUNTANT"]
GHOST = 987654  # an id that exists nowhere: an allowed call then answers 404 or 422, never 403


def denied(response) -> bool:
    return response.status_code == 403 and response.json().get("message") == DENIED


@pytest.fixture
def team(sign_in, staff_of, tenant_a, owner_login):
    """One signed-in client per system role, all in shop A."""
    clients = {"OWNER": sign_in(owner_login)}
    for code in ROLE_CODES[1:]:
        clients[code] = sign_in(staff_of(tenant_a, code))
    return clients


def concrete(template: str) -> str:
    import re

    return "/api/v1" + re.sub(r"\{[^}]+\}", str(GHOST), template)


class TestEveryRoleAgainstEveryRoute:
    def test_a_route_is_allowed_exactly_when_the_role_holds_what_it_needs(self, team, session_factory):
        from app.services import authorization_service

        with session_factory() as s:
            from app.models import Role

            held = {
                r.code: authorization_service.permissions_of_role(s, r)
                for r in s.query(Role).filter(Role.shop_id.is_(None))
            }
        wrong = []
        for rule in perms.ROUTE_RULES:
            for code, client in team.items():
                if rule.needs == (perms.MEMBER,):
                    continue
                response = client.request(
                    rule.method, concrete(rule.template), json={} if rule.method != "GET" else None
                )
                should_be_denied = not set(rule.needs) <= held[code]
                if denied(response) != should_be_denied:
                    wrong.append((code, rule.method, rule.template, response.status_code))
        assert not wrong, wrong[:10]

    def test_a_denial_is_the_same_plain_sentence_whatever_the_route(self, team):
        messages = {
            team["CASHIER"].get(concrete(p)).json()["message"]
            for p in ("/purchases", "/staff", "/audit-log", "/exports/sales")
        }
        assert messages == {DENIED}

    @pytest.mark.parametrize(
        ("role", "method", "path", "allowed"),
        [
            ("CASHIER", "GET", "/products", True),
            ("CASHIER", "POST", "/sales", True),
            ("CASHIER", "POST", "/quick-sales", True),
            ("CASHIER", "POST", f"/customers/{GHOST}/payments", True),
            ("CASHIER", "POST", f"/purchases/{GHOST}/post", False),
            ("CASHIER", "POST", "/inventory/opening-stock", False),
            ("CASHIER", "GET", "/reports/sales-summary", False),
            ("CASHIER", "GET", "/staff", False),
            ("CASHIER", "PATCH", "/shop", False),
            ("CASHIER", "GET", "/subscription", False),
            ("INVENTORY_STAFF", "POST", "/inventory/opening-stock", True),
            ("INVENTORY_STAFF", "GET", "/exports/inventory", True),
            ("INVENTORY_STAFF", "GET", "/reports/sales-summary", False),
            ("INVENTORY_STAFF", "GET", "/analytics/overview", False),
            ("INVENTORY_STAFF", "GET", "/exports/sales", False),
            ("INVENTORY_STAFF", "GET", "/purchases", False),
            ("SALES_STAFF", "POST", f"/sales/{GHOST}/post", True),
            ("SALES_STAFF", "POST", "/purchases", False),
            ("ACCOUNTANT", "GET", "/purchases", True),
            ("ACCOUNTANT", "GET", "/reports/sales-summary", True),
            ("ACCOUNTANT", "GET", "/exports/purchases", True),
            ("ACCOUNTANT", "POST", "/inventory/opening-stock", False),
            ("ACCOUNTANT", "PATCH", f"/products/{GHOST}", False),
            ("ACCOUNTANT", "POST", "/sales", False),
            ("MANAGER", "POST", "/staff/invitations", True),
            ("MANAGER", "POST", f"/purchases/{GHOST}/post", True),
            ("MANAGER", "POST", "/roles", False),
            ("OWNER", "POST", "/roles", True),
        ],
    )
    def test_the_stories_from_the_brief(self, team, role, method, path, allowed):
        response = team[role].request(method, "/api/v1" + path, json={} if method != "GET" else None)
        assert denied(response) is (not allowed), (role, method, path, response.status_code)


class TestRouteRulesCoverEverything:
    def test_every_business_route_has_exactly_one_rule_and_every_rule_has_a_route(self):
        from app.main import create_app

        spec = create_app().openapi()["paths"]
        routes = {
            (m.upper(), p.removeprefix("/api/v1"))
            for p, v in spec.items()
            for m in v
            if p.startswith("/api/v1")
        }
        guarded = {(m, p) for m, p in routes if not ("/api/v1" + p).startswith(perms.UNGUARDED_PREFIXES)}
        ruled = {(r.method, r.template) for r in perms.ROUTE_RULES}
        assert guarded - ruled == set(), f"routes without a permission rule: {sorted(guarded - ruled)}"
        assert ruled - guarded == set(), f"rules for routes that do not exist: {sorted(ruled - guarded)}"
        assert len(ruled) == len(perms.ROUTE_RULES), "a route has two rules"
        for method, template in guarded:
            assert perms.rule_for(method, concrete(template).removeprefix("/api/v1")).template == template, (
                method,
                template,
            )

    def test_every_permission_named_in_a_rule_exists(self):
        for rule in perms.ROUTE_RULES:
            assert set(rule.needs) <= perms.ALL_PERMISSIONS | {perms.MEMBER}, rule

    def test_the_defaults_in_the_database_match_the_documented_defaults(self, session_factory):
        from app.models import Role
        from app.services import authorization_service

        with session_factory() as s:
            for code, (_, _, expected) in perms.SYSTEM_ROLES.items():
                role = s.query(Role).filter(Role.shop_id.is_(None), Role.code == code).one()
                assert authorization_service.permissions_of_role(s, role) == expected, code

    def test_no_default_role_but_owner_holds_an_owner_only_permission(self):
        for code, (_, _, held) in perms.SYSTEM_ROLES.items():
            assert (code == "OWNER") == bool(held & perms.OWNER_ONLY_PERMISSIONS), code

    def test_a_route_with_no_rule_is_refused_not_allowed(self):
        assert (
            perms.rule_for("GET", "/nothing-like-this") is None
        )  # and _context_for_request answers 403 for it


class TestMembershipStates:
    def test_a_suspended_member_loses_access_at_once(self, team, tenant_a):
        cashier = team["CASHIER"]
        assert cashier.get("/api/v1/products").status_code == 200
        member_id = cashier.get("/api/v1/auth/me").json()["active_user_id"]
        assert team["OWNER"].post(f"/api/v1/staff/{member_id}/suspend").status_code == 200
        assert cashier.get("/api/v1/products").status_code == 401  # their sessions ended

    def test_a_membership_suspended_behind_the_sessions_back_is_refused(self, team, session_factory):
        cashier = team["CASHIER"]
        with session_factory() as s, s.begin():
            s.execute(text("UPDATE users SET status = 'SUSPENDED' WHERE email LIKE 'cashier%'"))
        response = cashier.get("/api/v1/products")
        assert (
            response.status_code == 403
            and response.json()["detail"]
            and "not active" in response.json()["message"]
        )

    def test_a_suspended_person_can_sign_in_but_reaches_no_shop(
        self, staff_of, tenant_a, real_client, sign_in
    ):
        email = staff_of(tenant_a, "CASHIER", status="SUSPENDED")
        client = real_client()
        body = client.post("/api/v1/auth/login", json={"email": email, "password": factories.PASSWORD}).json()
        assert body["memberships"] == []
        client.headers["X-CSRF-Token"] = body["csrf_token"]
        response = client.get("/api/v1/products")
        assert response.status_code == 403 and "any shop" in response.json()["message"]

    def test_a_removed_member_keeps_their_history_and_loses_access(self, team, session_factory, tenant_a):
        cashier = team["CASHIER"]
        member_id = cashier.get("/api/v1/auth/me").json()["active_user_id"]
        assert cashier.post("/api/v1/customers", json={"name": "Made By Cashier"}).status_code == 201
        assert team["OWNER"].post(f"/api/v1/staff/{member_id}/remove").json()["status"] == "REMOVED"
        assert cashier.get("/api/v1/products").status_code == 401
        with session_factory() as s:
            assert s.execute(
                text("SELECT status, removed_at IS NOT NULL FROM users WHERE id = :i"), {"i": member_id}
            ).one() == ("REMOVED", 1)
            assert (
                s.execute(
                    text("SELECT count(*) FROM audit_log WHERE user_id = :i"), {"i": member_id}
                ).scalar()
                >= 1
            )  # their history stays
        listed = team["OWNER"].get("/api/v1/staff", params={"include_removed": True}).json()["items"]
        assert member_id in [m["id"] for m in listed]
        assert member_id not in [m["id"] for m in team["OWNER"].get("/api/v1/staff").json()["items"]]

    def test_a_reactivated_member_signs_in_again(self, team, staff_of, real_client, tenant_a):
        cashier = team["CASHIER"]
        member_id = cashier.get("/api/v1/auth/me").json()["active_user_id"]
        team["OWNER"].post(f"/api/v1/staff/{member_id}/suspend")
        assert team["OWNER"].post(f"/api/v1/staff/{member_id}/reactivate").json()["status"] == "ACTIVE"
        again = real_client()
        assert (
            again.post(
                "/api/v1/auth/login",
                json={"email": f"cashier{tenant_a.shop.id}@test.local", "password": factories.PASSWORD},
            ).status_code
            == 200
        )

    def test_a_role_change_takes_effect_on_the_very_next_call(self, team):
        cashier = team["CASHIER"]
        member_id = cashier.get("/api/v1/auth/me").json()["active_user_id"]
        assert denied(cashier.get("/api/v1/purchases"))
        accountant = next(
            r for r in team["OWNER"].get("/api/v1/roles").json()["items"] if r["code"] == "ACCOUNTANT"
        )
        assert (
            team["OWNER"].patch(f"/api/v1/staff/{member_id}", json={"role_id": accountant["id"]}).status_code
            == 200
        )
        assert cashier.get("/api/v1/purchases").status_code == 200  # same session, new role
        assert denied(cashier.post("/api/v1/sales", json={}))


class TestManagingStaff:
    def role_id(self, owner, code):
        return next(r["id"] for r in owner.get("/api/v1/roles").json()["items"] if r["code"] == code)

    def test_nobody_changes_their_own_access(self, team):
        manager = team["MANAGER"]
        me = manager.get("/api/v1/auth/me").json()["active_user_id"]
        r = manager.patch(f"/api/v1/staff/{me}", json={"role_id": self.role_id(manager, "MANAGER")})
        assert r.status_code == 403 and "your own access" in r.json()["message"]
        assert manager.post(f"/api/v1/staff/{me}/suspend").status_code == 403

    def test_a_manager_cannot_manage_an_owner(self, team, session_factory, tenant_a):
        manager = team["MANAGER"]
        owner_id = team["OWNER"].get("/api/v1/auth/me").json()["active_user_id"]
        for action in ("suspend", "remove"):
            r = manager.post(f"/api/v1/staff/{owner_id}/{action}")
            assert r.status_code == 403 and r.json()["message"].startswith(
                "This member has access that you do not have"
            )
        assert (
            manager.patch(
                f"/api/v1/staff/{owner_id}", json={"role_id": self.role_id(manager, "CASHIER")}
            ).status_code
            == 403
        )

    def test_nobody_can_hand_out_more_than_they_hold(self, team):
        manager = team["MANAGER"]
        cashier_id = team["CASHIER"].get("/api/v1/auth/me").json()["active_user_id"]
        r = manager.patch(f"/api/v1/staff/{cashier_id}", json={"role_id": self.role_id(manager, "OWNER")})
        assert (
            r.status_code == 403
            and r.json()["message"] == "You can only give permissions that you have yourself."
        )
        invite = manager.post(
            "/api/v1/staff/invitations",
            json={"email": "new@example.com", "role_id": self.role_id(manager, "OWNER")},
        )
        assert invite.status_code == 403

    def test_one_owner_can_suspend_another_when_there_are_two(
        self, team, sign_in, session_factory, tenant_a, real_client
    ):
        owner = team["OWNER"]
        with session_factory() as s, s.begin():
            _, second = factories.make_login(s, tenant_a.shop, "OWNER", "second@owner.test")
        assert owner.post(f"/api/v1/staff/{second.id}/suspend").status_code == 200
        body = (
            real_client()
            .post("/api/v1/auth/login", json={"email": "second@owner.test", "password": factories.PASSWORD})
            .json()
        )
        assert body["memberships"] == []

    def test_a_shop_always_keeps_an_owner(self, session_factory, tenant_a):
        from app.core.context import RequestContext
        from app.models.enums import UserRole
        from app.services import staff_service
        from app.services.errors import ConflictError

        with session_factory() as s, s.begin():
            _, only_owner = factories.make_login(s, tenant_a.shop, "OWNER", "only@owner.test")
            _, cashier = factories.make_login(s, tenant_a.shop, "CASHIER", "c@test.local")
            role = s.execute(text("SELECT id FROM roles WHERE shop_id IS NULL AND code = 'CASHIER'")).scalar()
            actor = RequestContext(
                tenant_a.shop.id, cashier.id, UserRole.OWNER
            )  # an all-powerful actor who is not the target
            with pytest.raises(ConflictError, match="must always have an owner"):
                staff_service.change_role(s, actor, only_owner.id, role)
            with pytest.raises(ConflictError, match="must always have an owner"):
                staff_service.suspend(s, actor, only_owner.id)
            with pytest.raises(ConflictError, match="must always have an owner"):
                staff_service.remove(s, actor, only_owner.id)

    def test_another_shops_member_is_not_found(self, team, tenant_b, session_factory, staff_of):
        other = staff_of(tenant_b, "CASHIER")
        with session_factory() as s:
            other_id = s.execute(text("SELECT id FROM users WHERE email = :e"), {"e": other}).scalar()
        for call in (
            team["OWNER"].get(f"/api/v1/staff/{other_id}"),
            team["OWNER"].post(f"/api/v1/staff/{other_id}/suspend"),
            team["OWNER"].patch(f"/api/v1/staff/{other_id}", json={"role_id": 1}),
        ):
            assert call.status_code == 404

    def test_the_staff_list_shows_what_the_screen_needs_and_nothing_secret(self, team):
        body = team["OWNER"].get("/api/v1/staff").json()
        assert body["total"] == 6
        first = body["items"][0]
        assert {
            "name",
            "email",
            "role",
            "status",
            "joined_at",
            "last_active_at",
            "permission_count",
            "is_you",
        } <= set(first)
        assert "password" not in str(body).lower() and "hash" not in str(body).lower()
        detail = team["OWNER"].get(f"/api/v1/staff/{first['id']}").json()
        assert isinstance(detail["permissions"], list)


class TestCustomRoles:
    def perms_for(self, *codes):
        return list(codes)

    def test_an_owner_creates_edits_assigns_and_retires_a_role(self, team, session_factory):
        owner = team["OWNER"]
        made = owner.post(
            "/api/v1/roles",
            json={
                "name": "Store Supervisor",
                "description": "Runs the floor",
                "permissions": ["PRODUCT_VIEW", "SALE_VIEW", "SALE_CREATE"],
            },
        )
        assert (
            made.status_code == 201
            and made.json()["is_system"] is False
            and made.json()["code"] == "CUSTOM_STORE_SUPERVISOR"
        )
        role_id = made.json()["id"]
        cashier_id = team["CASHIER"].get("/api/v1/auth/me").json()["active_user_id"]
        assert owner.patch(f"/api/v1/staff/{cashier_id}", json={"role_id": role_id}).status_code == 200
        assert team["CASHIER"].get("/api/v1/sales").status_code == 200
        assert denied(team["CASHIER"].post(f"/api/v1/sales/{GHOST}/post"))  # the custom role has no SALE_POST
        owner.patch(
            f"/api/v1/roles/{role_id}",
            json={"permissions": ["PRODUCT_VIEW", "SALE_VIEW", "SALE_CREATE", "SALE_POST"]},
        )
        assert not denied(team["CASHIER"].post(f"/api/v1/sales/{GHOST}/post"))  # edited: takes effect at once
        refused = owner.post(f"/api/v1/roles/{role_id}/deactivate")
        assert refused.status_code == 409 and "still use this role" in refused.json()["message"]
        cashier_role = next(
            r["id"] for r in owner.get("/api/v1/roles").json()["items"] if r["code"] == "CASHIER"
        )
        owner.patch(f"/api/v1/staff/{cashier_id}", json={"role_id": cashier_role})
        assert owner.post(f"/api/v1/roles/{role_id}/deactivate").json()["is_active"] is False
        assert (
            owner.patch(f"/api/v1/staff/{cashier_id}", json={"role_id": role_id}).status_code == 422
        )  # a retired role cannot be given

    def test_a_custom_role_can_never_hold_an_owner_only_permission(self, team):
        for code in ("ROLE_MANAGE", "BACKUP_CREATE"):
            r = team["OWNER"].post(
                "/api/v1/roles", json={"name": f"Sneaky {code}", "permissions": ["PRODUCT_VIEW", code]}
            )
            assert r.status_code == 422 and "reserved for the owner" in r.json()["message"]

    def test_an_unknown_permission_is_refused(self, team):
        r = team["OWNER"].post(
            "/api/v1/roles", json={"name": "Odd", "permissions": ["PRODUCT_VIEW", "FLY_TO_MOON"]}
        )
        assert r.status_code == 422

    def test_only_the_owner_manages_roles_and_a_role_needs_a_name_and_permissions(self, team):
        assert denied(
            team["MANAGER"].post("/api/v1/roles", json={"name": "X", "permissions": ["PRODUCT_VIEW"]})
        )
        assert (
            team["OWNER"]
            .post("/api/v1/roles", json={"name": "  ", "permissions": ["PRODUCT_VIEW"]})
            .status_code
            == 422
        )
        assert (
            team["OWNER"].post("/api/v1/roles", json={"name": "Empty", "permissions": []}).status_code == 422
        )

    def test_system_roles_cannot_be_edited_and_another_shops_roles_are_invisible(
        self, team, sign_in, staff_of, tenant_b, session_factory
    ):
        owner = team["OWNER"]
        owner_role = next(
            r["id"] for r in owner.get("/api/v1/roles").json()["items"] if r["code"] == "CASHIER"
        )
        assert owner.patch(f"/api/v1/roles/{owner_role}", json={"name": "Hacked"}).status_code == 404
        with session_factory() as s, s.begin():
            _, b_owner = factories.make_login(s, tenant_b.shop, "OWNER", "b-owner@test.local")
        b = sign_in("b-owner@test.local")
        theirs = b.post("/api/v1/roles", json={"name": "Only In B", "permissions": ["PRODUCT_VIEW"]}).json()[
            "id"
        ]
        assert owner.get(f"/api/v1/roles/{theirs}").status_code == 404
        cashier_id = team["CASHIER"].get("/api/v1/auth/me").json()["active_user_id"]
        assert owner.patch(f"/api/v1/staff/{cashier_id}", json={"role_id": theirs}).status_code == 404
        assert "Only In B" not in str(owner.get("/api/v1/roles").json())

    def test_you_cannot_edit_the_permissions_of_the_role_you_hold(
        self, team, sign_in, session_factory, tenant_a
    ):
        owner = team["OWNER"]
        made = owner.post(
            "/api/v1/roles",
            json={"name": "Shift Lead", "permissions": ["PRODUCT_VIEW", "STAFF_VIEW", "STAFF_EDIT"]},
        ).json()
        with session_factory() as s, s.begin():
            factories.make_login(s, tenant_a.shop, "CASHIER", "lead@test.local")
            s.execute(
                text("UPDATE users SET role_id = :r WHERE email = 'lead@test.local'"), {"r": made["id"]}
            )
        lead = sign_in("lead@test.local")
        assert lead.get("/api/v1/staff").status_code == 200 and denied(
            lead.post("/api/v1/roles", json={"name": "z", "permissions": ["PRODUCT_VIEW"]})
        )
