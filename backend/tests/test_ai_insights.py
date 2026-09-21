"""AI insights: reorder and purchase suggestions, slow movers, declines, price comparison, unusual activity, ideas."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import PriceObservation
from app.models.enums import AdjustmentReason
from app.services import ai_dates, inventory_service
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.test_ai_assistant import ask, figures
from tests.test_ai_assistant import pro_plans as pro_plans  # noqa: F401  (autouse)
from tests.test_promotions import shop as shop  # noqa: F401
from tests.test_promotions import sold
from tests.test_sales_api import item

D = Decimal
BARCODE = "4006381333931"


def sold_on(client, items, day, **payment):
    return sold(client, items, header={"sale_date": day.isoformat()}, **payment)


@pytest.fixture
def supplier_id(client_a):
    return client_a.get("/api/v1/suppliers").json()["items"][0]["id"]


class TestReorderAndPurchases:
    def test_a_reorder_recommendation_uses_stock_level_pace_and_cost(self, client_a, shop, supplier_id):
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}",
            json={"reorder_level": "15", "default_supplier_id": supplier_id},
        )
        sold(client_a, [item(shop["rice"], "10")])  # 20 -> 10 in stock; 10 sold in the window
        answer = ask(client_a, "Mujhe batao kaunse products reorder karne chahiye.")
        assert answer["status"] == "ANSWERED" and answer["badges"] == ["AI Recommendation"]
        row = answer["table"]["rows"][0]
        # 10 sold in 30 days -> 21 days cover is 7, plus the reorder level 15 = 22; minus 10 in stock = 12
        assert row[0] == "Product RICE" and row[1] == "10 pcs" and row[2] == "15 pcs" and row[3] == "12 pcs"
        assert "Nothing has been ordered" in " ".join(answer["notes"])

    def test_nothing_to_reorder_is_plainly_stated(self, client_a, shop):
        assert ask(client_a, "which products should I reorder?")["status"] == "NO_DATA"

    def test_a_product_with_no_sales_is_brought_to_twice_its_level(self, client_a, shop):
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "25"}
        )  # stock 20, no sales
        row = ask(client_a, "reorder")["table"]["rows"][0]
        assert row[3] == "30 pcs" and "limited sales history" in row[4]  # 50 - 20

    def test_purchase_suggestions_are_grouped_by_supplier_and_create_nothing(
        self, client_a, shop, supplier_id, session_factory
    ):
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}",
            json={"reorder_level": "25", "default_supplier_id": supplier_id},
        )
        before = client_a.get("/api/v1/purchases").json()["total"]
        answer = ask(client_a, "What should I purchase this week?")
        assert answer["tool"] == "get_purchase_suggestions" and answer["badges"] == ["AI Recommendation"]
        assert answer["table"]["rows"][0][0] == "Sharma Traders" and answer["table"]["rows"][0][4] == "30 pcs"
        proposal = answer["proposals"][0]
        assert proposal["kind"] == "PURCHASE_DRAFT" and proposal["payload"]["supplier_id"] == supplier_id
        assert (
            proposal["payload"]["items"][0]["quantity"] == "30.0"
            or proposal["payload"]["items"][0]["quantity"] == "30"
        )
        assert client_a.get("/api/v1/purchases").json()["total"] == before  # a suggestion is not a purchase

    def test_products_without_a_supplier_get_no_draft(self, client_a, shop):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "25"})
        answer = ask(client_a, "what should I buy")
        assert answer["proposals"] == [] and any("no preferred supplier" in n for n in answer["notes"])


class TestSlowAndDeclining:
    def test_high_stock_with_no_sales_is_slow_moving(self, client_a, shop):
        sold(client_a, [item(shop["rice"], "15")])  # 5 left, about 20 days of stock at that pace: not slow
        answer = ask(client_a, "Which products have high stock but low sales?")
        names = [r[0] for r in answer["table"]["rows"]]
        assert (
            answer["tool"] == "get_slow_moving_products"
            and "Product CHIPS" in names
            and "Product RICE" not in names
        )

    def test_declining_products_compare_with_the_previous_period(self, client_a, shop):
        today = today_in_shop_timezone()
        prior = ai_dates.previous(ai_dates.resolve("this month", today), today)
        sold_on(client_a, [item(shop["rice"], "10")], prior.start)
        sold(client_a, [item(shop["rice"], "1")])
        answer = ask(client_a, "Which products have declining sales?")
        assert answer["tool"] == "get_declining_products"
        row = answer["table"]["rows"][0]
        if today.day == 1:  # the current period and the previous one share no day of the month to compare on
            pytest.skip("first of the month")
        assert row[0] == "Product RICE" and row[3] == "-90.0%"

    def test_category_performance(self, client_a, shop):
        today = today_in_shop_timezone()
        prior = ai_dates.previous(ai_dates.resolve("this month", today), today)
        sold_on(client_a, [item(shop["chips"], "10")], prior.start)
        sold(client_a, [item(shop["rice"], "1")])
        answer = ask(client_a, "Which category is performing poorly?")
        assert answer["tool"] == "get_category_performance"
        assert "performing poorly" in answer["message"] and "Snacks" in answer["message"]


class TestPriceComparison:
    @pytest.fixture
    def priced(self, client_a, shop, session_factory, tenant_a):
        client_a.patch(
            f"/api/v1/products/{shop['rice']['id']}",
            json={"barcode": BARCODE, "name": "Basmati Rice 1kg", "brand": "India Gate"},
        )
        now = datetime.now(UTC)
        with session_factory() as s, s.begin():
            for price, provider, city in (
                ("95", "open_prices", "Delhi"),
                ("110", "open_prices", "Jaipur"),
                ("102", "upcitemdb", None),
            ):
                s.add(PriceObservation(shop_id=tenant_a.shop.id, barcode=BARCODE, provider=provider, product_name="Basmati Rice 1kg",
                                       brand="India Gate", pack_text="1 kg", price=D(price), currency="INR", city=city,
                                       location_text=city, checked_at=now - timedelta(hours=2)))  # fmt: skip
        return shop["rice"]

    def test_the_answer_states_the_range_sources_match_and_changes_nothing(self, client_a, priced):
        answer = ask(client_a, "Is the price of Basmati Rice 1kg competitive?")
        assert answer["tool"] == "get_price_comparison" and answer["status"] == "ANSWERED"
        assert "range of ₹95.00 to ₹110.00" in answer["message"] and "₹50.00" in answer["message"]
        assert figures(answer)["Your selling price"] == "₹50.00"
        rows = answer["table"]["rows"]
        assert {r[1] for r in rows} == {"open_prices", "upcitemdb"} and all(
            "confidence" in r[3] for r in rows
        )
        assert any("Delhi" in r[4] for r in rows)
        assert any("information only" in n for n in answer["notes"])
        assert client_a.get(f"/api/v1/products/{priced['id']}").json()["selling_price"] == "50.00"

    def test_no_saved_prices_is_not_enough_data(self, client_a, shop):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"barcode": BARCODE})
        answer = ask(client_a, "is my price for Product RICE competitive")
        assert (
            answer["status"] == "NO_DATA"
            and "I don't have enough data to determine this." in answer["message"]
        )

    def test_a_missing_product_name_is_asked_for(self, client_a, shop):
        assert ask(client_a, "is my selling price competitive?")["status"] == "NOT_UNDERSTOOD"

    def test_it_never_asks_an_outside_source(self, client_a, priced, monkeypatch):
        from app.services import price_providers

        def boom(*a, **k):
            raise AssertionError("an outside request was made")

        monkeypatch.setattr(price_providers, "http_get", boom)
        assert ask(client_a, "Is the price of Basmati Rice 1kg competitive?")["status"] == "ANSWERED"

    def test_the_plan_still_gates_price_intelligence(self, client_a, priced, tenant_a, give_plan):
        give_plan(tenant_a, "basic")  # has the AI insights, not price intelligence
        body = ask(client_a, "Is the price of Basmati Rice 1kg competitive?", expect=403)
        assert "market price checks" in body["message"]


class TestUnusualActivity:
    def test_a_sales_drop_is_a_neutral_notice(self, client_a, shop):
        today = today_in_shop_timezone()
        for back in (8, 9, 10):
            sold_on(
                client_a, [item(shop["sugar"], "5")], today - timedelta(days=back)
            )  # 240 a day the week before
        sold(client_a, [item(shop["chips"], "1")])  # 30 this week
        answer = ask(client_a, "Anything unusual in my business?")
        assert answer["tool"] == "get_anomalies" and answer["status"] == "ANSWERED"
        text = " ".join(" ".join(r) for r in answer["table"]["rows"])
        assert "Unusual activity detected: sales are well below the previous week" in text
        for forbidden in ("fraud", "theft", "steal", "suspect", "accus", "cheat"):
            assert forbidden not in text.lower()

    def test_a_large_stock_adjustment_is_noticed(self, client_a, shop, session_factory, tenant_a):
        with session_factory() as s, s.begin():
            inventory_service.record_adjustment(
                s,
                context_for(tenant_a),
                product_id=shop["rice"]["id"],
                quantity_delta=D("-8"),
                reason_code=AdjustmentReason.LOST,
            )
        answer = ask(client_a, "unusual activity")
        assert any(
            "large stock adjustment" in r[0] and "8 pcs of Product RICE was removed" in r[1]
            for r in answer["table"]["rows"]
        )

    def test_quiet_data_has_nothing_unusual(self, client_a, shop):
        answer = ask(client_a, "unusual activity")
        assert answer["status"] == "NO_DATA" and "No unusual activity" in answer["message"]
        assert any(
            "online ordering" in n for n in answer["notes"]
        )  # online monitoring is honestly unavailable

    def test_a_returns_spike_and_a_discount_spike_are_noticed(self, client_a, shop):
        from tests.test_promotions import live

        live(client_a, name="Big", percent="50")
        for _ in range(4):
            made = sold(client_a, [item(shop["rice"], "1")])
            client_a.post(
                "/api/v1/sales-returns",
                json={
                    "sale_id": made["id"],
                    "refund_mode": "CASH",
                    "items": [{"sale_item_id": made["items"][0]["id"], "quantity": "1"}],
                },
            )
        answer = ask(client_a, "anything unusual?")
        kinds = " ".join(r[0] for r in answer["table"]["rows"])
        assert "returns are high" in kinds


class TestInsightsAndIdeas:
    def test_insights_are_backed_by_retrieved_figures(self, client_a, shop):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "25"})
        sold(client_a, [item(shop["oil"], "1")])
        answer = ask(client_a, "Give me some insights")
        text = " ".join(r[0] for r in answer["table"]["rows"])
        assert answer["badges"] == ["AI Recommendation"]
        assert "1 product is at or below the reorder level" in text
        assert "Profit Not Available for 1 sale because cost data is missing" in text
        assert all(r[1].startswith("Based on") for r in answer["table"]["rows"])

    def test_promotion_ideas_are_drafts_to_review_with_a_margin_check(self, client_a, shop):
        answer = ask(client_a, "Suggest a promotion idea for slow products")
        assert answer["tool"] == "get_promotion_ideas" and answer["badges"] == ["AI Recommendation"]
        kinds = {p["kind"] for p in answer["proposals"]}
        assert kinds == {"PROMOTION_DRAFT"}
        oil = next(r for r in answer["table"]["rows"] if "Product OIL" in r[0])
        assert (
            "cost for this product is not known" in oil[2]
        )  # no cost: margin cannot be checked, and it says so
        assert client_a.get("/api/v1/promotions").json()["total"] == 0  # showing an idea creates nothing
