"""AI actions: the assistant proposes, a person confirms, an existing service executes, and everything is audited."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import AiAction, AuditLog, InventoryTransaction, Promotion, Purchase, Sale
from app.services import inventory_service
from tests.test_ai_assistant import API, ask
from tests.test_ai_assistant import pro_plans as pro_plans  # noqa: F401  (autouse)
from tests.test_promotions import shop as shop  # noqa: F401
from tests.test_promotions import sold
from tests.test_purchases_api import stock
from tests.test_sales_api import item

D = Decimal


@pytest.fixture
def supplier_id(client_a):
    return client_a.get("/api/v1/suppliers").json()["items"][0]["id"]


def purchase_payload(shop, supplier_id, quantity="25", cost="20"):
    return {
        "supplier_id": supplier_id,
        "items": [{"product_id": shop["rice"]["id"], "quantity": quantity, "unit_cost": cost}],
    }


def propose(client, kind, payload, feature="assistant", expect=201):
    response = client.post(f"{API}/actions", json={"kind": kind, "feature": feature, "payload": payload})
    assert response.status_code == expect, response.text
    return response.json()


def confirm(client, action, expect=200):
    response = client.post(f"{API}/actions/{action['id']}/confirm")
    assert response.status_code == expect, response.text
    return response.json()


def totals(session_factory):
    with session_factory() as s:
        return tuple(
            s.scalar(select(func.count()).select_from(m))
            for m in (Purchase, InventoryTransaction, Promotion, Sale)
        )


def audit(session_factory, action_id):
    with session_factory() as s:
        rows = s.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "ai_action", AuditLog.entity_id == action_id)
            .order_by(AuditLog.id)
        )
        return [(r.action, r.after_json, r.before_json) for r in rows]


class TestPurchaseDraft:
    def test_proposing_shows_an_exact_preview_and_creates_nothing(
        self, client_a, shop, supplier_id, session_factory
    ):
        before = totals(session_factory)
        action = propose(
            client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id), feature="purchase_suggestions"
        )
        p = action["preview"]
        assert (
            action["status"] == "PROPOSED" and p["title"] == "Create Purchase Draft" and p["shop"] == "Shop A"
        )
        assert p["supplier"] == "Sharma Traders" and p["totals"] == [
            {"label": "Estimated total", "value": "₹500.00"}
        ]
        assert p["lines"]["rows"] == [["Product RICE", "25 pcs", "₹20.00", "₹500.00"]]
        assert "purchase DRAFT" in p["impact"][0] and "Nothing is posted" in p["impact"][1]
        assert p["can_confirm"] is True and p["problems"] == []
        assert totals(session_factory) == before

    def test_confirming_creates_a_draft_through_purchase_service_and_never_posts_it(
        self, client_a, shop, supplier_id, session_factory
    ):
        action = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        done = confirm(client_a, action)
        assert done["status"] == "EXECUTED" and done["result_type"] == "purchase"
        purchase = client_a.get(f"/api/v1/purchases/{done['result_ids'][0]}").json()
        assert (
            purchase["status"] == "DRAFT"
            and purchase["total_amount"] == "500.00"
            and purchase["purchase_no"] is None
        )
        assert "AI assistant" in purchase["notes"] and len(purchase["items"]) == 1
        assert stock(client_a, shop["rice"]) == 20  # nothing moved: it is only a draft

    def test_the_audit_trail_records_who_what_feature_result_and_outcome(
        self, client_a, shop, supplier_id, session_factory, tenant_a
    ):
        action = propose(
            client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id), feature="purchase_suggestions"
        )
        done = confirm(client_a, action)
        trail = audit(session_factory, action["id"])
        assert [a for a, *_ in trail] == ["propose", "confirm"]
        proposed, confirmed = trail[0][1], trail[1][1]
        assert proposed["feature"] == "purchase_suggestions" and proposed["kind"] == "PURCHASE_DRAFT"
        assert confirmed["outcome"] == "success" and confirmed["result"] == {
            "type": "purchase",
            "ids": done["result_ids"],
        }
        assert confirmed["proposed"] == confirmed["confirmed"]
        with session_factory() as s:
            row = s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "ai_action", AuditLog.action == "confirm")
            ).one()
            assert (row.shop_id, row.user_id) == (tenant_a.shop.id, tenant_a.user.id)
            assert s.get(AiAction, action["id"]).decided_by == tenant_a.user.id

    def test_editing_changes_what_would_happen_and_keeps_the_first_proposal(
        self, client_a, shop, supplier_id, session_factory
    ):
        action = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        edited = client_a.patch(
            f"{API}/actions/{action['id']}",
            json={"payload": purchase_payload(shop, supplier_id, quantity="40", cost="19")},
        )
        assert edited.status_code == 200
        assert edited.json()["preview"]["totals"][0]["value"] == "₹760.00"
        done = confirm(client_a, action)
        assert client_a.get(f"/api/v1/purchases/{done['result_ids'][0]}").json()["total_amount"] == "760.00"
        with session_factory() as s:
            saved = s.get(AiAction, action["id"])
            assert (
                saved.proposal["items"][0]["quantity"] == "25"
                and saved.current["items"][0]["quantity"] == "40"
            )
        assert [a for a, *_ in audit(session_factory, action["id"])] == ["propose", "edit", "confirm"]

    def test_cancelling_creates_nothing_and_closes_the_action(
        self, client_a, shop, supplier_id, session_factory
    ):
        before = totals(session_factory)
        action = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        cancelled = client_a.post(f"{API}/actions/{action['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
        assert confirm(client_a, action, expect=409)["error_code"] == "action_closed"
        assert (
            client_a.patch(
                f"{API}/actions/{action['id']}", json={"payload": purchase_payload(shop, supplier_id)}
            ).status_code
            == 409
        )
        assert totals(session_factory) == before
        assert [a for a, *_ in audit(session_factory, action["id"])] == ["propose", "cancel"]

    def test_confirming_twice_creates_one_draft(self, client_a, shop, supplier_id, session_factory):
        action = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        confirm(client_a, action)
        assert confirm(client_a, action, expect=409)["message"] == "This action is already executed."
        assert totals(session_factory)[0] == 4  # the fixture's three stock-up purchases plus one draft

    def test_a_line_without_a_price_cannot_be_confirmed(self, client_a, shop, supplier_id, session_factory):
        before = totals(session_factory)
        payload = purchase_payload(shop, supplier_id)
        payload["items"][0]["unit_cost"] = None
        action = propose(client_a, "PURCHASE_DRAFT", payload)
        assert (
            action["preview"]["can_confirm"] is False
            and "Enter the price" in action["preview"]["problems"][0]
        )
        assert confirm(client_a, action, expect=422)["detail"][0]["msg"].startswith("Enter the price")
        assert totals(session_factory) == before
        with session_factory() as s:
            assert s.get(AiAction, action["id"]).status.value == "FAILED"  # the failed attempt is on record
        # fixing the price makes the same action confirmable again
        fixed = client_a.patch(
            f"{API}/actions/{action['id']}", json={"payload": purchase_payload(shop, supplier_id)}
        ).json()
        assert fixed["status"] == "PROPOSED" and confirm(client_a, action)["status"] == "EXECUTED"

    @pytest.mark.parametrize(
        "bad", [{"quantity": "0"}, {"quantity": "-3"}, {"unit_cost": "-1"}, {"quantity": "abc"}]
    )
    def test_invalid_quantities_and_prices_are_refused_when_proposing(self, client_a, shop, supplier_id, bad):
        payload = purchase_payload(shop, supplier_id)
        payload["items"][0].update(bad)
        propose(client_a, "PURCHASE_DRAFT", payload, expect=422)

    def test_unknown_fields_and_features_are_refused(self, client_a, shop, supplier_id):
        payload = purchase_payload(shop, supplier_id)
        payload["post_immediately"] = True  # not a thing the assistant can ask for
        propose(client_a, "PURCHASE_DRAFT", payload, expect=422)
        propose(
            client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id), feature="made_up", expect=422
        )
        assert (
            client_a.post(
                f"{API}/actions", json={"kind": "POST_PURCHASE", "feature": "assistant", "payload": {}}
            ).status_code
            == 422
        )


class TestStockAdjustment:
    def payload(self, shop, counted="17", system="20", reason="COUNT_CORRECTION"):
        return {
            "items": [
                {
                    "product_id": shop["rice"]["id"],
                    "counted_quantity": counted,
                    "system_quantity": system,
                    "reason_code": reason,
                }
            ]
        }

    def test_the_preview_states_the_exact_change(self, client_a, shop):
        action = propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop), feature="stock_list_photo")
        p = action["preview"]
        assert p["lines"]["rows"] == [["Product RICE", "20 pcs", "17 pcs", "-3", "Count Correction"]]
        assert (
            "1 inventory adjustment" in p["impact"][0]
            and "net -3 units" in p["impact"][0]
            and p["can_confirm"]
        )

    def test_confirming_adjusts_stock_through_inventory_service_with_a_reason(
        self, client_a, shop, session_factory
    ):
        action = propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop))
        assert stock(client_a, shop["rice"]) == 20  # proposing changed nothing
        done = confirm(client_a, action)
        assert done["result_type"] == "inventory_adjustment" and stock(client_a, shop["rice"]) == 17
        with session_factory() as s:
            row = s.get(InventoryTransaction, done["result_ids"][0])
            assert (row.txn_type.value, row.qty_delta, row.reason_code.value) == (
                "ADJUSTMENT",
                D("-3"),
                "COUNT_CORRECTION",
            )
            assert "AI assistant" in row.note

    def test_stock_that_moved_since_the_draft_blocks_it_until_refreshed(
        self, client_a, shop, session_factory
    ):
        action = propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop))
        sold(client_a, [item(shop["rice"], "2")])  # stock is now 18
        fresh = client_a.get(f"{API}/actions/{action['id']}").json()
        assert (
            fresh["preview"]["can_confirm"] is False
            and "has changed since this was prepared" in fresh["preview"]["problems"][0]
        )
        confirm(client_a, action, expect=422)
        assert stock(client_a, shop["rice"]) == 18
        refreshed = client_a.post(f"{API}/actions/{action['id']}/refresh-stock").json()
        assert (
            refreshed["preview"]["lines"]["rows"][0][1] == "18 pcs"
            and refreshed["preview"]["lines"]["rows"][0][3] == "-1"
        )
        assert confirm(client_a, action)["status"] == "EXECUTED" and stock(client_a, shop["rice"]) == 17

    def test_a_matching_count_has_nothing_to_adjust(self, client_a, shop):
        action = propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop, counted="20"))
        assert (
            action["preview"]["can_confirm"] is False and "no difference" in action["preview"]["problems"][0]
        )
        confirm(client_a, action, expect=422)

    def test_other_needs_a_note_and_a_duplicate_line_is_refused(self, client_a, shop):
        action = propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop, reason="OTHER"))
        assert "Describe the reason" in action["preview"]["problems"][0]
        both = {"items": self.payload(shop)["items"] * 2}
        assert any(
            "appears twice" in p for p in propose(client_a, "STOCK_ADJUSTMENT", both)["preview"]["problems"]
        )

    def test_it_cannot_take_stock_below_zero_by_a_count(self, client_a, shop):
        propose(client_a, "STOCK_ADJUSTMENT", self.payload(shop, counted="-1"), expect=422)


class TestPromotionDraft:
    def test_a_draft_offer_is_created_but_not_activated(self, client_a, shop, session_factory):
        payload = {
            "name": "10% off Chips",
            "promo_type": "PERCENT",
            "scope": "PRODUCTS",
            "percent": "10",
            "product_ids": [shop["chips"]["id"]],
        }
        action = propose(client_a, "PROMOTION_DRAFT", payload, feature="promotion_ideas")
        assert "activate it" in action["preview"]["impact"][1]
        done = confirm(client_a, action)
        promo = client_a.get(f"/api/v1/promotions/{done['result_ids'][0]}").json()
        assert promo["status"] == "DRAFT" and promo["is_live"] is False and promo["terms"] == "10% off"
        assert (
            client_a.post("/api/v1/sales/calculate", json={"items": [item(shop["chips"], "1")]}).json()[
                "promotion_discount"
            ]
            == "0.00"
        )

    def test_only_simple_kinds_and_valid_scopes_are_allowed(self, client_a, shop):
        propose(
            client_a,
            "PROMOTION_DRAFT",
            {"name": "x", "promo_type": "BUY_X_GET_Y", "percent": "10"},
            expect=422,
        )
        empty = propose(
            client_a,
            "PROMOTION_DRAFT",
            {"name": "x", "promo_type": "PERCENT", "scope": "PRODUCTS", "percent": "10"},
        )
        assert empty["preview"]["can_confirm"] is False

    def test_a_plan_without_offers_refuses_the_draft(self, client_a, shop, tenant_a, give_plan):
        give_plan(tenant_a, "free")  # AI basics, but no offers
        action = propose(
            client_a,
            "PROMOTION_DRAFT",
            {"name": "x", "promo_type": "PERCENT", "percent": "10"},
            feature="assistant",
        )
        body = confirm(client_a, action, expect=403)
        assert "promotions and coupons" in body["message"]


class TestFailuresAndSafety:
    def test_a_service_refusal_is_recorded_on_the_action_and_in_the_audit(
        self, client_a, shop, session_factory
    ):
        action = propose(client_a, "STOCK_ADJUSTMENT", TestStockAdjustment().payload(shop))
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}/active", json={"is_active": False}
        ) if False else None
        with session_factory() as s, s.begin():
            from app.models import Product

            s.get(Product, shop["rice"]["id"]).is_active = False
        confirm(client_a, action, expect=422)
        trail = audit(session_factory, action["id"])
        assert (
            trail[-1][0] == "confirm_failed"
            and trail[-1][1]["outcome"] == "failure"
            and "inactive" in trail[-1][1]["message"]
        )
        with session_factory() as s:
            saved = s.get(AiAction, action["id"])
            assert (
                saved.status.value == "FAILED" and saved.attempts == 1 and "inactive" in saved.failure_message
            )

    def test_an_unexpected_failure_rolls_back_and_shows_the_same_reference_everywhere(
        self, client_a, shop, session_factory, monkeypatch
    ):
        action = propose(client_a, "STOCK_ADJUSTMENT", TestStockAdjustment().payload(shop))
        before = totals(session_factory)

        def boom(*_a, **_k):
            raise RuntimeError("failed in /Users/me/secret.py with api_key=sk-secret-99999")

        real = inventory_service.record_adjustment
        monkeypatch.setattr(inventory_service, "record_adjustment", boom)
        quiet = TestClient(app, raise_server_exceptions=False)
        quiet.app.dependency_overrides = client_a.app.dependency_overrides
        response = quiet.post(f"{API}/actions/{action['id']}/confirm")
        body = response.json()
        assert (
            response.status_code == 500
            and body["reference_id"].startswith("ERR-")
            and "secret" not in str(body)
        )
        assert totals(session_factory) == before and stock(client_a, shop["rice"]) == 20  # nothing kept
        with session_factory() as s:
            saved = s.get(AiAction, action["id"])
            assert saved.status.value == "FAILED" and saved.reference_id == body["reference_id"]
        assert audit(session_factory, action["id"])[-1][1]["reference_id"] == body["reference_id"]
        monkeypatch.setattr(inventory_service, "record_adjustment", real)
        assert (
            confirm(client_a, action)["status"] == "EXECUTED"
        )  # it stays open and works once the cause is gone

    def test_another_shops_products_and_actions_are_unreachable(
        self, client_a, client_b, shop, supplier_id, tenant_b
    ):
        propose(
            client_b, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id), expect=404
        )  # A's supplier and product
        action = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        assert client_b.get(f"{API}/actions/{action['id']}").status_code == 404
        assert client_b.post(f"{API}/actions/{action['id']}/confirm").status_code == 404
        assert client_b.post(f"{API}/actions/{action['id']}/cancel").status_code == 404
        assert client_b.get(f"{API}/actions").json()["items"] == []

    def test_only_the_owner_can_propose_or_confirm(self, make_client, tenant_a, shop, supplier_id):
        from app.models.enums import UserRole

        staff = make_client(tenant_a, UserRole.STAFF) if hasattr(UserRole, "STAFF") else None
        if staff is None:
            pytest.skip("only an owner role exists")
        assert (
            staff.post(
                f"{API}/actions",
                json={
                    "kind": "PURCHASE_DRAFT",
                    "feature": "assistant",
                    "payload": purchase_payload(shop, supplier_id),
                },
            ).status_code
            == 403
        )

    def test_an_assistant_answer_can_be_turned_into_a_draft_by_a_person(self, client_a, shop, supplier_id):
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}",
            json={"reorder_level": "25", "default_supplier_id": supplier_id},
        )
        proposal = ask(client_a, "What should I purchase this week?")["proposals"][0]
        action = propose(client_a, proposal["kind"], proposal["payload"], feature=proposal["feature"])
        assert action["preview"]["shop"] == "Shop A"
        assert (
            client_a.get("/api/v1/purchases").json()["total"] == 3
        )  # still only the fixture's own purchases
        confirm(client_a, action)
        assert client_a.get("/api/v1/purchases").json()["total"] == 4

    def test_no_delete_and_no_post_endpoints_exist_for_actions(self, client_a):
        assert client_a.delete(f"{API}/actions/1").status_code == 405
        schema = client_a.get("/openapi.json").json()["paths"]
        ai_paths = [p for p in schema if p.startswith("/api/v1/ai")]
        assert ai_paths and not any(
            "post" in p.split("/")[-1] or "activate" in p or "void" in p for p in ai_paths
        )
        assert all("delete" not in methods for p, methods in schema.items() if p.startswith("/api/v1/ai"))

    def test_the_action_list_shows_the_history(self, client_a, shop, supplier_id):
        a = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        b = propose(client_a, "PURCHASE_DRAFT", purchase_payload(shop, supplier_id))
        confirm(client_a, a)
        client_a.post(f"{API}/actions/{b['id']}/cancel")
        rows = client_a.get(f"{API}/actions").json()["items"]
        assert [(r["id"], r["status"]) for r in rows] == [(b["id"], "CANCELLED"), (a["id"], "EXECUTED")]
        assert [
            r["id"] for r in client_a.get(f"{API}/actions", params={"status": "EXECUTED"}).json()["items"]
        ] == [a["id"]]
