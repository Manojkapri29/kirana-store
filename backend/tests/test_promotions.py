"""Promotions and coupons through the API: management, billing, snapshots, limits, eligibility, isolation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Promotion, QuickSale, Sale, SaleItem, SalePromotion
from app.models.enums import PromotionScope, PromotionStatus, PromotionType, SaleStatus, UserRole
from tests import factories
from tests.conftest import assert_rejected
from tests.factories import today_in_shop_timezone
from tests.test_business_types import REQUESTED_TYPES
from tests.test_purchases_api import make_product, make_supplier, stock
from tests.test_sales_api import CUSTOMERS, INV, item, make_customer, owed, stock_up

D = Decimal
API = "/api/v1/promotions"
SALES = "/api/v1/sales"


def errors(response) -> dict[str, str]:
    return {".".join(str(p) for p in i["loc"][1:]): i["msg"] for i in response.json()["detail"]}


def when(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def create(client, **body):
    body = {"name": "Festival Offer", "promo_type": "PERCENT", "percent": "10", **body}
    response = client.post(API, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def live(client, **body):
    made = create(client, **body)
    response = client.post(f"{API}/{made['id']}/activate")
    assert response.status_code == 200, response.text
    return response.json()


def state(client, promotion, action):
    return client.post(f"{API}/{promotion['id']}/{action}")


def calc(client, items, **extra):
    response = client.post(f"{SALES}/calculate", json={"items": items, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def draft(client, items, **header):
    response = client.post(SALES, json={"items": items, **header})
    assert response.status_code == 201, response.text
    return response.json()


def post(client, sale, **payment):
    payment.setdefault("payment_method", "CASH")
    return client.post(f"{SALES}/{sale['id']}/post", json=payment)


def sold(client, items, *, header=None, **payment):
    response = post(client, draft(client, items, **(header or {})), **payment)
    assert response.status_code == 200, response.text
    return response.json()


def sale_detail(client, sale):
    return client.get(f"{SALES}/{sale['id']}").json()


@pytest.fixture(autouse=True)
def plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


@pytest.fixture
def shop(client_a, tenant_a, units, session_factory):
    """Rice 50/pcs (cost 20, 20 in stock), sugar 48/kg (cost 20, 100 kg), oil 140/L (cost unknown, 20 L),
    and a snack in a second category: 30/pcs (cost 10, 50 in stock)."""
    supplier = make_supplier(client_a)
    with session_factory() as s, s.begin():
        snacks = factories.make_category(s, tenant_a.shop, "Snacks")
    rice = make_product(client_a, tenant_a, units, "RICE", selling_price="50", mrp="55")
    sugar = make_product(client_a, tenant_a, units, "SUGAR", unit="kg", selling_price="48")
    oil = make_product(client_a, tenant_a, units, "OIL", unit="L", selling_price="140")
    chips = make_product(client_a, tenant_a, units, "CHIPS", selling_price="30", category_id=snacks.id)
    stock_up(client_a, supplier, rice, 20, 20)
    stock_up(client_a, supplier, sugar, 100, 20)
    stock_up(client_a, supplier, chips, 50, 10)
    client_a.post(f"{INV}/opening-stock", json={"product_id": oil["id"], "quantity": "20"})
    return {"rice": rice, "sugar": sugar, "oil": oil, "chips": chips, "snacks_id": snacks.id}


class TestManagement:
    def test_a_promotion_starts_as_a_draft_and_applies_to_nothing(self, client_a, shop):
        made = create(client_a, description="Diwali")
        assert made["status"] == "DRAFT" and made["effective_status"] == "DRAFT" and made["is_live"] is False
        assert made["terms"] == "10% off" and made["percent"] == "10.00" and made["used_count"] == 0
        assert calc(client_a, [item(shop["rice"], "2")])["promotion_discount"] == "0.00"

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"promo_type": "PERCENT", "percent": None}, "percent"),
            ({"promo_type": "PERCENT", "percent": "0"}, "percent"),
            ({"promo_type": "PERCENT", "percent": "100.01"}, "percent"),
            ({"promo_type": "PERCENT", "percent": "10.005"}, "percent"),
            ({"promo_type": "PERCENT", "percent": "10", "amount": "5"}, "amount"),
            ({"promo_type": "AMOUNT", "percent": None, "amount": "0"}, "amount"),
            ({"promo_type": "AMOUNT", "percent": None}, "amount"),
            ({"promo_type": "OFFER_PRICE", "percent": None, "offer_price": "9"}, "scope"),
            (
                {"promo_type": "BUY_X_GET_Y", "percent": None, "scope": "PRODUCTS", "buy_quantity": 2},
                "get_quantity",
            ),
            ({"scope": "PRODUCTS"}, "product_ids"),
            ({"scope": "CATEGORIES"}, "category_ids"),
            ({"scope": "PRODUCTS", "product_ids": [999999]}, "product_ids"),
            ({"scope": "CATEGORIES", "category_ids": [999999]}, "category_ids"),
            ({"audience": "CUSTOMERS"}, "customer_ids"),
            ({"audience": "CUSTOMERS", "customer_ids": [999999]}, "customer_ids"),
            ({"customer_ids": [1]}, "customer_ids"),
            ({"product_ids": [1]}, "scope"),
            ({"starts_at": when(days=2), "ends_at": when(days=1)}, "ends_at"),
            ({"coupon_code": "A B"}, "coupon_code"),
            ({"coupon_code": "AB"}, "coupon_code"),
            ({"coupon_code": "x" * 31}, "coupon_code"),
            ({"min_cart_value": "-1"}, "min_cart_value"),
            ({"min_quantity": "0"}, "min_quantity"),
            ({"max_discount": "0"}, "max_discount"),
            ({"usage_limit": 0}, "usage_limit"),
            ({"per_customer_limit": 0}, "per_customer_limit"),
            ({"name": "   "}, "name"),
            ({"priority": 1001}, "priority"),
        ],
    )
    def test_bad_definitions_are_refused_with_the_field_named(self, client_a, shop, body, field):
        response = client_a.post(API, json={"name": "X", "promo_type": "PERCENT", "percent": "10", **body})
        assert response.status_code == 422, response.text
        assert field in errors(response)

    def test_percent_must_be_text_not_a_number_with_decimals(self, client_a):
        response = client_a.post(API, json={"name": "X", "promo_type": "PERCENT", "percent": 12.5})
        assert response.status_code == 422

    def test_unknown_fields_are_refused(self, client_a):
        assert (
            client_a.post(
                API, json={"name": "X", "promo_type": "PERCENT", "percent": "5", "status": "ACTIVE"}
            ).status_code
            == 422
        )

    def test_every_promotion_type_can_be_defined(self, client_a, shop):
        rice = shop["rice"]["id"]
        made = [
            create(client_a, name="P"),
            create(client_a, name="A", promo_type="AMOUNT", percent=None, amount="30"),
            create(client_a, name="O", promo_type="OFFER_PRICE", percent=None, offer_price="45", scope="PRODUCTS", product_ids=[rice]),
            create(client_a, name="B", promo_type="BUY_X_GET_Y", percent=None, buy_quantity=2, get_quantity=1, scope="PRODUCTS", product_ids=[rice]),
            create(client_a, name="C", scope="CATEGORIES", category_ids=[shop["snacks_id"]]),
        ]  # fmt: skip
        assert [m["terms"] for m in made] == [
            "10% off", "30.00 off", "Offer price 45.00", "Buy 2 get 1 free", "10% off"
        ]  # fmt: skip
        assert made[4]["categories"] == ["Snacks"] and made[2]["products"] == ["Product RICE"]

    def test_lifecycle_activate_pause_resume_expire(self, client_a):
        made = create(client_a)
        assert state(client_a, made, "pause").status_code == 409  # a draft cannot be paused
        active = state(client_a, made, "activate").json()
        assert active["status"] == "ACTIVE" and active["is_live"] is True
        assert state(client_a, made, "activate").status_code == 409
        assert state(client_a, made, "pause").json()["status"] == "PAUSED"
        assert state(client_a, made, "activate").json()["status"] == "ACTIVE"
        ended = state(client_a, made, "expire").json()
        assert ended["status"] == "EXPIRED" and ended["is_live"] is False
        for action in ("activate", "pause", "expire"):
            assert state(client_a, made, action).status_code == 409

    def test_an_expired_promotion_cannot_be_edited(self, client_a):
        made = create(client_a)
        state(client_a, made, "expire")
        assert client_a.patch(f"{API}/{made['id']}", json={"name": "Again"}).status_code == 409

    def test_an_offer_past_its_end_date_reads_as_expired_and_cannot_be_activated(
        self, client_a, session_factory
    ):
        made = create(client_a, starts_at=when(days=-3), ends_at=when(days=-2))
        assert made["effective_status"] == "DRAFT"
        assert state(client_a, made, "activate").status_code == 409
        with session_factory() as s, s.begin():  # it was active, and its end date has since passed
            s.get(Promotion, made["id"]).status = PromotionStatus.ACTIVE
        assert client_a.get(f"{API}/{made['id']}").json()["effective_status"] == "EXPIRED"
        assert client_a.get(API, params={"status": "EXPIRED"}).json()["total"] == 1
        assert client_a.get(API, params={"status": "ACTIVE"}).json()["total"] == 0

    def test_edit_a_promotion(self, client_a, shop):
        made = live(client_a, coupon_code="save10")
        assert made["coupon_code"] == "SAVE10"
        edited = client_a.patch(
            f"{API}/{made['id']}",
            json={"name": "Better", "percent": "15", "max_discount": "100", "priority": 4},
        )
        assert edited.status_code == 200, edited.text
        data = edited.json()
        assert (data["name"], data["percent"], data["max_discount"], data["priority"], data["status"]) == (
            "Better",
            "15.00",
            "100.00",
            4,
            "ACTIVE",
        )
        assert data["coupon_code"] == "SAVE10"  # unchanged fields survive a partial update
        cleared = client_a.patch(
            f"{API}/{made['id']}", json={"coupon_code": None, "max_discount": None}
        ).json()
        assert cleared["coupon_code"] is None and cleared["max_discount"] is None

    def test_the_kind_and_scope_cannot_be_changed(self, client_a):
        made = create(client_a)
        assert client_a.patch(f"{API}/{made['id']}", json={"promo_type": "AMOUNT"}).status_code == 422
        assert client_a.patch(f"{API}/{made['id']}", json={"scope": "PRODUCTS"}).status_code == 422

    def test_editing_that_leaves_an_invalid_promotion_is_refused(self, client_a):
        made = create(client_a)
        assert client_a.patch(f"{API}/{made['id']}", json={"amount": "5"}).status_code == 422
        assert client_a.patch(f"{API}/{made['id']}", json={"percent": None}).status_code == 422
        assert client_a.get(f"{API}/{made['id']}").json()["percent"] == "10.00"

    def test_there_is_no_delete(self, client_a):
        assert client_a.delete(f"{API}/{create(client_a)['id']}").status_code == 405

    def test_a_coupon_code_is_unique_per_shop_ignoring_case_but_reusable_across_shops(
        self, client_a, client_b
    ):
        create(client_a, coupon_code="SAVE10")
        clash = client_a.post(
            API, json={"name": "Two", "promo_type": "PERCENT", "percent": "5", "coupon_code": "save10"}
        )
        assert clash.status_code == 409 and "coupon_code" in errors(clash)
        other = client_a.post(
            API, json={"name": "Two", "promo_type": "PERCENT", "percent": "5", "coupon_code": "SAVE20"}
        ).json()
        assert client_a.patch(f"{API}/{other['id']}", json={"coupon_code": "Save10"}).status_code == 409
        assert create(client_b, coupon_code="SAVE10")["coupon_code"] == "SAVE10"

    def test_list_search_and_filters(self, client_a):
        a = live(client_a, name="Diwali Sale", coupon_code="DIWALI")
        b = create(client_a, name="Weekend", promo_type="AMOUNT", percent=None, amount="20")
        c = live(client_a, name="Holi")
        state(client_a, c, "pause")
        ids = lambda **p: sorted(x["id"] for x in client_a.get(API, params=p).json()["items"])  # noqa: E731
        assert ids() == sorted([a["id"], b["id"], c["id"]])
        assert ids(q="diwali") == [a["id"]] and ids(q="wee") == [b["id"]] and ids(q="%") == []
        assert (
            ids(status="ACTIVE") == [a["id"]]
            and ids(status="PAUSED") == [c["id"]]
            and ids(status="DRAFT") == [b["id"]]
        )
        assert ids(promo_type="AMOUNT") == [b["id"]]
        assert ids(coupon_only="true") == [a["id"]] and ids(coupon_only="false") == sorted([b["id"], c["id"]])
        assert client_a.get(API, params={"status": "BOGUS"}).status_code == 422
        page = client_a.get(API, params={"limit": 2, "offset": 1}).json()
        assert len(page["items"]) == 2 and page["total"] == 3

    def test_changes_are_audited(self, client_a, fresh):
        made = create(client_a)
        state(client_a, made, "activate")
        client_a.patch(f"{API}/{made['id']}", json={"name": "Renamed"})
        actions = fresh(
            lambda s: [
                a.action
                for a in s.scalars(
                    select(AuditLog).where(AuditLog.entity_type == "promotion").order_by(AuditLog.id)
                )
            ]
        )
        assert actions == ["create", "activate", "update"]


class TestAccessAndPlans:
    def test_staff_can_read_but_not_change_offers(self, client_a, make_client, tenant_a):
        made = live(client_a)
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert staff.get(API).status_code == 200 and staff.get(f"{API}/{made['id']}").status_code == 200
        assert staff.get(f"{API}/usage").status_code == 200
        body = {"name": "X", "promo_type": "PERCENT", "percent": "5"}
        assert staff.post(API, json=body).status_code == 403
        assert staff.patch(f"{API}/{made['id']}", json={"name": "Y"}).status_code == 403
        for action in ("activate", "pause", "expire"):
            assert staff.post(f"{API}/{made['id']}/{action}").status_code == 403

    def test_staff_still_get_the_offers_when_billing(self, client_a, make_client, tenant_a, shop):
        live(client_a)
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert calc(staff, [item(shop["rice"], "2")])["promotion_discount"] == "10.00"

    def test_a_plan_without_offers_cannot_create_or_activate_them(self, client_a, tenant_a, give_plan):
        made = create(client_a)
        give_plan(tenant_a, "free")
        refused = client_a.post(API, json={"name": "X", "promo_type": "PERCENT", "percent": "5"})
        assert refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "promotions"
        assert state(client_a, made, "activate").status_code == 403
        assert client_a.patch(f"{API}/{made['id']}", json={"name": "Y"}).status_code == 403
        assert client_a.get(f"{API}/{made['id']}").status_code == 200  # reading is still fine

    def test_billing_works_and_gives_no_discount_when_the_plan_has_no_offers(
        self, client_a, tenant_a, give_plan, shop
    ):
        live(client_a)
        give_plan(tenant_a, "free")
        sale = sold(client_a, [item(shop["rice"], "2")])
        assert (
            sale["promotion_discount"] == "0.00"
            and sale["total_amount"] == "100.00"
            and sale["promotions"] == []
        )

    def test_a_coupon_needs_the_plan_too(self, client_a, tenant_a, give_plan, shop):
        live(client_a, coupon_code="SAVE10")
        give_plan(tenant_a, "free")
        assert client_a.post(SALES, json={"items": [], "coupon_code": "SAVE10"}).status_code == 403
        preview = calc(client_a, [item(shop["rice"])], coupon_code="SAVE10")
        assert preview["coupon"]["applied"] is False and "does not include" in preview["coupon"]["message"]


class TestBillingPreviewAndPosting:
    def test_the_preview_shows_subtotal_discount_total_and_the_reason(self, client_a, shop):
        live(client_a, name="Festival Offer", percent="10")
        preview = calc(client_a, [item(shop["rice"], "2"), item(shop["sugar"], "1")])
        assert (
            preview["subtotal"],
            preview["discount"],
            preview["promotion_discount"],
            preview["total"],
        ) == ("148.00", "0.00", "14.80", "133.20")
        applied = preview["promotions"][0]
        assert (applied["name"], applied["terms"], applied["amount"]) == (
            "Festival Offer",
            "10% off",
            "14.80",
        )
        assert "the whole bill" in applied["basis"] and applied["coupon_code"] is None
        assert [line["promotion_discount"] for line in preview["lines"]] == ["10.00", "4.80"]
        assert preview["coupon"] is None and preview["not_applied"] == []

    def test_the_preview_never_saves_anything(self, client_a, shop, fresh):
        live(client_a)
        calc(client_a, [item(shop["rice"], "2")])
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(SalePromotion))) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Sale))) == 0

    def test_posting_freezes_the_offer_on_the_sale(self, client_a, shop):
        live(client_a, name="Festival Offer", percent="10")
        sale = sold(client_a, [item(shop["rice"], "2"), item(shop["sugar"], "1")])
        assert (sale["subtotal"], sale["discount"], sale["promotion_discount"], sale["total_amount"]) == (
            "148.00",
            "0.00",
            "14.80",
            "133.20",
        )
        assert sale["amount_paid"] == "133.20" and sale["payment_type"] == "PAID"
        [snapshot] = sale["promotions"]
        assert (snapshot["name"], snapshot["terms"], snapshot["amount"], snapshot["coupon_code"]) == (
            "Festival Offer",
            "10% off",
            "14.80",
            None,
        )
        assert [i["promotion_discount"] for i in sale["items"]] == ["10.00", "4.80"]
        assert [i["net_total"] for i in sale["items"]] == ["90.00", "43.20"]

    def test_line_profit_and_sale_profit_use_what_was_really_earned(self, client_a, shop):
        live(client_a, percent="10")
        sale = sold(client_a, [item(shop["rice"], "2")])  # 100 less 10 = 90; cost 2 x 20 = 40
        assert sale["cogs_total"] == "40.00" and sale["gross_profit"] == "50.00"
        assert sale["items"][0]["profit"] == "50.00" and sale["items"][0]["line_total"] == "100.00"

    def test_an_offer_never_changes_the_products_prices_or_cost(self, client_a, shop):
        live(client_a, percent="50")
        url = f"/api/v1/products/{shop['rice']['id']}"
        before = client_a.get(url).json()
        sold(client_a, [item(shop["rice"], "3")])
        after = client_a.get(url).json()
        for key in ("mrp", "selling_price", "purchase_price", "avg_cost"):
            assert after[key] == before[key], key
        assert (after["mrp"], after["selling_price"]) == ("55.00", "50.00")
        assert stock(client_a, shop["rice"]) == 17

    def test_the_posted_total_is_the_paid_amount_and_the_credit_is_on_the_net_total(self, client_a, shop):
        live(client_a, percent="10")
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shop["rice"], "2")], header={"customer_id": customer["id"]}, amount_paid="40"
        )
        assert sale["total_amount"] == "90.00" and sale["credit_amount"] == "50.00"
        assert owed(client_a, customer) == D("50.00")

    def test_stock_and_cost_are_unaffected_by_the_offer(self, client_a, shop):
        live(client_a, percent="90")
        sale = sold(client_a, [item(shop["rice"], "4")])
        assert sale["total_amount"] == "20.00" and sale["promotion_discount"] == "180.00"
        assert stock(client_a, shop["rice"]) == 16
        assert sale["items"][0]["unit_cost"] == "20.00" and sale["cogs_total"] == "80.00"
        assert sale["gross_profit"] == "-60.00"  # the offer was deeper than the margin: reported, not hidden

    def test_a_bill_that_an_offer_brings_to_zero_cannot_be_posted(self, client_a, shop):
        live(client_a, percent="100")
        preview = calc(client_a, [item(shop["rice"], "2")])
        assert (preview["promotion_discount"], preview["total"]) == ("100.00", "0.00")
        refused = post(client_a, draft(client_a, [item(shop["rice"], "2")]))
        assert refused.status_code == 422 and "greater than zero" in refused.text
        assert stock(client_a, shop["rice"]) == 20

    def test_a_bill_discount_and_an_offer_are_two_separate_amounts(self, client_a, shop):
        live(client_a, percent="10")
        sale = sold(client_a, [item(shop["rice"], "4")], header={"discount": "20"})
        assert (sale["subtotal"], sale["discount"], sale["promotion_discount"], sale["total_amount"]) == (
            "200.00",
            "20.00",
            "20.00",
            "160.00",
        )

    def test_the_offer_can_never_take_the_bill_below_zero_after_the_cashiers_discount(self, client_a, shop):
        live(client_a, percent="100")
        preview = calc(client_a, [item(shop["rice"], "2")], discount="30")
        assert (preview["discount"], preview["promotion_discount"], preview["total"]) == (
            "30.00",
            "70.00",
            "0.00",
        )
        sale = draft(client_a, [item(shop["rice"], "2")], discount="30")
        assert (sale["discount"], sale["promotion_discount"], sale["total_amount"]) == (
            "30.00",
            "70.00",
            "0.00",
        )

    def test_a_line_discount_and_an_offer_combine_on_what_is_left(self, client_a, shop):
        live(client_a, percent="10")
        sale = sold(client_a, [item(shop["rice"], "2", discount="20")])  # line = 80
        assert sale["promotion_discount"] == "8.00" and sale["total_amount"] == "72.00"

    def test_there_is_no_way_to_send_a_promotion_discount(self, client_a, shop):
        for extra in ({"promotion_discount": "5"}, {"total_amount": "1"}, {"promotions": []}):
            assert client_a.post(SALES, json={"items": [], **extra}).status_code == 422
        assert (
            client_a.post(
                SALES, json={"items": [{**item(shop["rice"]), "promotion_discount": "5"}]}
            ).status_code
            == 422
        )
        draft_sale = draft(client_a, [item(shop["rice"])])
        assert (
            client_a.patch(f"{SALES}/{draft_sale['id']}", json={"promotion_discount": "5"}).status_code == 422
        )

    def test_a_draft_shows_its_offers_and_they_follow_edits(self, client_a, shop):
        live(client_a, percent="10")
        sale = draft(client_a, [item(shop["rice"], "2")])
        assert (sale["promotion_discount"], sale["total_amount"]) == ("10.00", "90.00")
        assert sale["promotions"][0]["amount"] == "10.00" and sale["promotions_out_of_date"] is False
        more = client_a.put(f"{SALES}/{sale['id']}/items", json={"items": [item(shop["rice"], "4")]}).json()
        assert (more["promotion_discount"], more["total_amount"]) == ("20.00", "180.00")
        empty = client_a.put(f"{SALES}/{sale['id']}/items", json={"items": []}).json()
        assert (empty["promotion_discount"], empty["total_amount"]) == ("0.00", "0.00")

    def test_a_draft_notices_when_its_offers_have_changed_and_posting_uses_the_truth(self, client_a, shop):
        made = live(client_a, percent="10")
        sale = draft(client_a, [item(shop["rice"], "2")])
        state(client_a, made, "pause")
        stale = sale_detail(client_a, sale)
        assert stale["promotion_discount"] == "10.00" and stale["promotions_out_of_date"] is True
        posted = post(client_a, sale).json()
        assert (
            posted["promotion_discount"] == "0.00"
            and posted["total_amount"] == "100.00"
            and posted["promotions"] == []
        )

    def test_changing_the_customer_re_evaluates_customer_offers(self, client_a, shop):
        vip = make_customer(client_a, "VIP")
        other = make_customer(client_a, "Other")
        live(client_a, audience="CUSTOMERS", customer_ids=[vip["id"]], percent="20")
        sale = draft(client_a, [item(shop["rice"], "2")], customer_id=vip["id"])
        assert sale["promotion_discount"] == "20.00"
        assert (
            client_a.patch(f"{SALES}/{sale['id']}", json={"customer_id": other["id"]}).json()[
                "promotion_discount"
            ]
            == "0.00"
        )
        assert (
            client_a.patch(f"{SALES}/{sale['id']}", json={"customer_id": None}).json()["promotion_discount"]
            == "0.00"
        )

    def test_the_posted_sale_lists_show_the_net_total(self, client_a, shop):
        live(client_a, percent="10")
        sold(client_a, [item(shop["rice"], "2")])
        [row] = client_a.get(SALES).json()["items"]
        assert row["total_amount"] == "90.00"

    def test_a_voided_sale_keeps_its_snapshot_and_the_stock_and_khata_reverse(self, client_a, shop):
        live(client_a, percent="10")
        customer = make_customer(client_a)
        sale = sold(
            client_a, [item(shop["rice"], "2")], header={"customer_id": customer["id"]}, amount_paid="0"
        )
        voided = client_a.post(f"{SALES}/{sale['id']}/void", json={"reason": "Wrong"}).json()
        assert voided["status"] == "VOID" and voided["promotions"][0]["amount"] == "10.00"
        assert (
            voided["total_amount"] == "90.00"
            and owed(client_a, customer) == 0
            and stock(client_a, shop["rice"]) == 20
        )


class TestPromotionKindsInBilling:
    def test_an_offer_price_on_a_product(self, client_a, shop):
        live(
            client_a,
            name="Rice Offer",
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="45",
            scope="PRODUCTS",
            product_ids=[shop["rice"]["id"]],
        )
        sale = sold(client_a, [item(shop["rice"], "3"), item(shop["sugar"], "1")])
        assert sale["promotion_discount"] == "15.00" and sale["total_amount"] == "183.00"
        assert sale["items"][0]["unit_price"] == "50.00"  # the selling price is not overwritten
        assert sale["items"][0]["mrp"] == "55.00"  # neither is the MRP
        assert sale["promotions"][0]["terms"] == "Offer price 45.00"

    def test_buy_two_get_one_free(self, client_a, shop):
        live(
            client_a,
            name="B2G1",
            promo_type="BUY_X_GET_Y",
            percent=None,
            buy_quantity=2,
            get_quantity=1,
            scope="PRODUCTS",
            product_ids=[shop["rice"]["id"]],
        )
        assert calc(client_a, [item(shop["rice"], "2")])["promotion_discount"] == "0.00"
        sale = sold(client_a, [item(shop["rice"], "3")])
        assert sale["promotion_discount"] == "50.00" and sale["total_amount"] == "100.00"
        assert stock(client_a, shop["rice"]) == 17  # the free unit still leaves the shelf

    def test_a_category_offer_touches_only_that_category(self, client_a, shop):
        live(client_a, name="Snack Day", scope="CATEGORIES", category_ids=[shop["snacks_id"]], percent="50")
        sale = sold(client_a, [item(shop["rice"], "2"), item(shop["chips"], "4")])
        assert sale["promotion_discount"] == "60.00" and sale["total_amount"] == "160.00"
        assert [i["promotion_discount"] for i in sale["items"]] == ["0.00", "60.00"]
        assert "1 eligible item" in sale["promotions"][0]["basis"]

    def test_a_fixed_amount_off_a_product(self, client_a, shop):
        live(
            client_a,
            promo_type="AMOUNT",
            percent=None,
            amount="15",
            scope="PRODUCTS",
            product_ids=[shop["sugar"]["id"]],
        )
        assert calc(client_a, [item(shop["rice"]), item(shop["sugar"], "2")])["promotion_discount"] == "15.00"

    def test_a_minimum_bill_offer(self, client_a, shop):
        live(client_a, name="Big Bill", percent="10", min_cart_value="500")
        under = calc(client_a, [item(shop["rice"], "9")])
        assert under["promotion_discount"] == "0.00"
        assert under["not_applied"][0]["reason"].startswith("Needs a bill of at least 500.00")
        assert calc(client_a, [item(shop["rice"], "10")])["promotion_discount"] == "50.00"

    def test_a_minimum_quantity_offer(self, client_a, shop):
        live(client_a, percent="10", scope="PRODUCTS", product_ids=[shop["rice"]["id"]], min_quantity="5")
        assert calc(client_a, [item(shop["rice"], "4")])["promotion_discount"] == "0.00"
        assert calc(client_a, [item(shop["rice"], "5")])["promotion_discount"] == "25.00"

    def test_a_maximum_discount_caps_the_offer(self, client_a, shop):
        live(client_a, percent="50", max_discount="30")
        preview = calc(client_a, [item(shop["rice"], "4")])
        assert (
            preview["promotion_discount"] == "30.00"
            and preview["promotions"][0]["terms"] == "50% off (up to 30.00)"
        )

    def test_the_offer_uses_the_price_the_cashier_charged(self, client_a, shop):
        live(
            client_a,
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="45",
            scope="PRODUCTS",
            product_ids=[shop["rice"]["id"]],
        )
        cheaper = calc(client_a, [item(shop["rice"], "2", unit_price="46")])
        assert cheaper["promotion_discount"] == "2.00"
        already = calc(client_a, [item(shop["rice"], "2", unit_price="44")])
        assert already["promotion_discount"] == "0.00"


class TestStackingInBilling:
    def test_the_higher_priority_exclusive_offer_wins(self, client_a, shop):
        live(client_a, name="Small", percent="5", priority=1)
        live(client_a, name="Big", percent="20", priority=9)
        preview = calc(client_a, [item(shop["rice"], "2")])
        assert [p["name"] for p in preview["promotions"]] == ["Big"] and preview[
            "promotion_discount"
        ] == "20.00"
        assert (
            preview["not_applied"][0]["name"] == "Small" and "combined" in preview["not_applied"][0]["reason"]
        )

    def test_stackable_offers_combine_in_order(self, client_a, shop):
        live(
            client_a,
            name="Item",
            percent="50",
            scope="PRODUCTS",
            product_ids=[shop["rice"]["id"]],
            stackable=True,
        )
        live(client_a, name="Bill", percent="10", stackable=True)
        sale = sold(client_a, [item(shop["rice"], "2")])
        assert [p["name"] for p in sale["promotions"]] == ["Item", "Bill"]
        assert [p["amount"] for p in sale["promotions"]] == ["50.00", "5.00"]
        assert sale["promotion_discount"] == "55.00" and sale["total_amount"] == "45.00"
        assert sale["items"][0]["promotion_discount"] == "55.00"

    def test_a_coupon_and_an_automatic_offer_combine_only_if_both_are_stackable(self, client_a, shop):
        live(client_a, name="Auto", percent="10", stackable=True, priority=2)
        live(client_a, name="Coupon", percent="10", stackable=True, priority=1, coupon_code="EXTRA")
        both = calc(client_a, [item(shop["rice"], "2")], coupon_code="EXTRA")
        assert [p["name"] for p in both["promotions"]] == ["Auto", "Coupon"] and both[
            "promotion_discount"
        ] == "19.00"
        without = calc(client_a, [item(shop["rice"], "2")])
        assert [p["name"] for p in without["promotions"]] == ["Auto"]

    def test_an_exclusive_coupon_replaces_lower_priority_automatic_offers(self, client_a, shop):
        live(client_a, name="Auto", percent="10", priority=1)
        live(client_a, name="Coupon", percent="30", priority=5, coupon_code="BIG")
        preview = calc(client_a, [item(shop["rice"], "2")], coupon_code="BIG")
        assert [p["name"] for p in preview["promotions"]] == ["Coupon"] and preview["coupon"][
            "applied"
        ] is True


class TestCoupons:
    def test_a_coupon_offer_applies_only_when_its_code_is_entered(self, client_a, shop):
        live(client_a, name="Save", percent="10", coupon_code="SAVE10")
        assert calc(client_a, [item(shop["rice"], "2")])["promotion_discount"] == "0.00"
        entered = calc(client_a, [item(shop["rice"], "2")], coupon_code="save10")
        assert (
            entered["promotion_discount"] == "10.00" and entered["promotions"][0]["coupon_code"] == "SAVE10"
        )
        assert entered["coupon"] == {"code": "SAVE10", "applied": True, "message": "Coupon SAVE10 applied."}
        assert (
            calc(client_a, [item(shop["rice"], "2")], coupon_code="  Save10 ")["promotion_discount"]
            == "10.00"
        )

    def test_the_coupon_is_recorded_on_the_sale_and_its_snapshot(self, client_a, shop):
        live(client_a, name="Save", percent="10", coupon_code="SAVE10")
        sale = sold(client_a, [item(shop["rice"], "2")], header={"coupon_code": "save10"})
        assert sale["coupon_code"] == "SAVE10" and sale["promotions"][0]["coupon_code"] == "SAVE10"
        assert sale["total_amount"] == "90.00"

    def test_an_unknown_coupon_is_refused_on_a_draft_and_explained_in_the_preview(self, client_a, shop):
        live(client_a, coupon_code="SAVE10")
        refused = client_a.post(SALES, json={"items": [], "coupon_code": "NOPE"})
        assert refused.status_code == 422 and "coupon_code" in errors(refused)
        preview = calc(client_a, [item(shop["rice"])], coupon_code="NOPE")
        assert preview["coupon"] == {"code": "NOPE", "applied": False, "message": "Coupon code not found."}

    def test_a_coupon_can_be_removed_from_a_draft(self, client_a, shop):
        live(client_a, percent="10", coupon_code="SAVE10")
        sale = draft(client_a, [item(shop["rice"], "2")], coupon_code="SAVE10")
        assert sale["promotion_discount"] == "10.00"
        removed = client_a.patch(f"{SALES}/{sale['id']}", json={"coupon_code": None}).json()
        assert removed["promotion_discount"] == "0.00" and removed["coupon_code"] is None

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"status": "paused"}, "paused"),
            ({"status": "expired"}, "expired"),
            ({"starts_at": when(days=1)}, "not started"),
            ({"ends_at": when(seconds=1)}, "expired"),
            ({"min_cart_value": "5000"}, "at least 5000.00"),
        ],
    )
    def test_a_coupon_that_no_longer_applies_stops_the_sale_instead_of_being_dropped(
        self, client_a, shop, session_factory, change, reason
    ):
        made = live(client_a, percent="10", coupon_code="SAVE10")
        sale = draft(client_a, [item(shop["rice"], "2")], coupon_code="SAVE10")
        assert sale["promotion_discount"] == "10.00"
        with session_factory() as s, s.begin():
            row = s.get(Promotion, made["id"])
            if change.get("status") == "paused":
                row.status = PromotionStatus.PAUSED
            elif change.get("status") == "expired":
                row.status = PromotionStatus.EXPIRED
            elif "starts_at" in change:
                row.starts_at = datetime.fromisoformat(change["starts_at"])
            elif "ends_at" in change:
                row.ends_at = datetime.fromisoformat(change["ends_at"]) + timedelta(seconds=0)
                row.ends_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                row.min_cart_value = D(change["min_cart_value"])
        response = post(client_a, sale)
        assert response.status_code == 422, response.text
        message = errors(response)["coupon_code"]
        assert reason in message.lower() and "Remove the coupon" in message
        assert sale_detail(client_a, sale)["status"] == "DRAFT" and stock(client_a, shop["rice"]) == 20
        assert client_a.patch(f"{SALES}/{sale['id']}", json={"coupon_code": None}).status_code == 200
        assert post(client_a, sale).json()["promotion_discount"] == "0.00"

    def test_another_shops_coupon_is_not_found(self, client_a, client_b, shop):
        live(client_b, percent="90", coupon_code="THEIRS")
        assert client_a.post(SALES, json={"items": [], "coupon_code": "THEIRS"}).status_code == 422
        preview = calc(client_a, [item(shop["rice"])], coupon_code="THEIRS")
        assert (
            preview["promotion_discount"] == "0.00"
            and preview["coupon"]["message"] == "Coupon code not found."
        )

    def test_a_corrected_copy_keeps_the_coupon_and_re_evaluates_it(self, client_a, shop):
        made = live(client_a, percent="10", coupon_code="SAVE10")
        sale = sold(client_a, [item(shop["rice"], "2")], header={"coupon_code": "SAVE10"})
        client_a.post(f"{SALES}/{sale['id']}/void", json={"reason": "Typo"})
        copy = client_a.post(f"{SALES}/{sale['id']}/correct").json()
        assert copy["coupon_code"] == "SAVE10" and copy["promotion_discount"] == "10.00"
        state(client_a, made, "pause")
        assert post(client_a, copy).status_code == 422


class TestUsageLimits:
    def test_the_total_usage_limit(self, client_a, shop):
        live(client_a, percent="10", coupon_code="ONCE", usage_limit=1)
        first = draft(client_a, [item(shop["rice"])], coupon_code="ONCE")
        second = draft(client_a, [item(shop["rice"])], coupon_code="ONCE")
        assert (
            first["promotion_discount"] == second["promotion_discount"] == "5.00"
        )  # a draft uses nothing up
        assert post(client_a, first).json()["promotion_discount"] == "5.00"
        refused = post(client_a, second)
        assert refused.status_code == 422 and "most times it allows" in errors(refused)["coupon_code"]
        assert sale_detail(client_a, second)["status"] == "DRAFT"
        assert calc(client_a, [item(shop["rice"])], coupon_code="ONCE")["coupon"]["applied"] is False
        assert draft(client_a, [item(shop["rice"])], coupon_code="ONCE")["promotion_discount"] == "0.00"

    def test_an_automatic_offer_stops_applying_when_its_limit_is_used_up(self, client_a, shop):
        live(client_a, percent="10", usage_limit=2)
        for _ in range(2):
            assert sold(client_a, [item(shop["rice"])])["promotion_discount"] == "5.00"
        assert sold(client_a, [item(shop["rice"])])["promotion_discount"] == "0.00"

    def test_voiding_a_sale_gives_its_use_back(self, client_a, shop):
        live(client_a, percent="10", usage_limit=1)
        first = sold(client_a, [item(shop["rice"])])
        assert calc(client_a, [item(shop["rice"])])["promotion_discount"] == "0.00"
        client_a.post(f"{SALES}/{first['id']}/void", json={"reason": "Oops"})
        assert calc(client_a, [item(shop["rice"])])["promotion_discount"] == "5.00"

    def test_a_draft_does_not_use_up_an_offer_but_a_discarded_one_never_did(self, client_a, shop):
        made = live(client_a, percent="10", usage_limit=1)
        d = draft(client_a, [item(shop["rice"])])
        client_a.post(f"{SALES}/{d['id']}/void", json={"reason": "no"})
        assert client_a.get(f"{API}/{made['id']}").json()["used_count"] == 0

    def test_the_per_customer_limit(self, client_a, shop):
        live(client_a, percent="10", per_customer_limit=1, coupon_code="ONEEACH")
        asha, ravi = make_customer(client_a, "Asha"), make_customer(client_a, "Ravi")
        assert (
            sold(
                client_a, [item(shop["rice"])], header={"customer_id": asha["id"], "coupon_code": "ONEEACH"}
            )["promotion_discount"]
            == "5.00"
        )
        again = post(
            client_a, draft(client_a, [item(shop["rice"])], customer_id=asha["id"], coupon_code="ONEEACH")
        )
        assert again.status_code == 422 and "This customer has already used" in errors(again)["coupon_code"]
        assert (
            sold(
                client_a, [item(shop["rice"])], header={"customer_id": ravi["id"], "coupon_code": "ONEEACH"}
            )["promotion_discount"]
            == "5.00"
        )

    def test_a_per_customer_limit_needs_a_customer(self, client_a, shop):
        live(client_a, percent="10", per_customer_limit=1, coupon_code="ONEEACH")
        preview = calc(client_a, [item(shop["rice"])], coupon_code="ONEEACH")
        assert preview["coupon"]["applied"] is False and "Choose a customer" in preview["coupon"]["message"]

    @pytest.mark.parametrize("attempt", range(3))
    def test_two_simultaneous_sales_cannot_both_take_the_last_use(
        self, client_a, make_client, tenant_a, shop, attempt
    ):
        made = live(client_a, percent="10", coupon_code=f"LAST{attempt}", usage_limit=1)
        drafts = [draft(client_a, [item(shop["rice"])], coupon_code=f"LAST{attempt}") for _ in range(3)]
        clients = [make_client(tenant_a) for _ in drafts]
        barrier = threading.Barrier(len(drafts))

        def run(pair):
            client, sale = pair
            barrier.wait()
            return post(client, sale).status_code

        with ThreadPoolExecutor(len(drafts)) as pool:
            codes = sorted(pool.map(run, zip(clients, drafts, strict=True)))

        assert codes == [200, 422, 422]
        assert client_a.get(f"{API}/{made['id']}").json()["used_count"] == 1


class TestEligibility:
    def test_only_an_active_offer_inside_its_dates_applies(self, client_a, shop):
        cart = [item(shop["rice"], "2")]
        create(client_a, name="Draft")
        paused = live(client_a, name="Paused")
        state(client_a, paused, "pause")
        expired = live(client_a, name="Expired")
        state(client_a, expired, "expire")
        live(client_a, name="Future", starts_at=when(days=1))
        assert calc(client_a, cart)["promotion_discount"] == "0.00"
        current = live(client_a, name="Now", starts_at=when(days=-1), ends_at=when(days=1))
        assert [p["name"] for p in calc(client_a, cart)["promotions"]] == ["Now"]
        state(client_a, current, "pause")
        assert calc(client_a, cart)["promotions"] == []

    def test_an_offer_whose_end_date_passes_stops_applying_without_anyone_touching_it(
        self, client_a, shop, session_factory
    ):
        made = live(client_a, name="Short", ends_at=when(days=1))
        assert calc(client_a, [item(shop["rice"])])["promotion_discount"] == "5.00"
        with session_factory() as s, s.begin():
            s.get(Promotion, made["id"]).ends_at = datetime.now(UTC) - timedelta(seconds=1)
        assert calc(client_a, [item(shop["rice"])])["promotion_discount"] == "0.00"
        assert post(client_a, draft(client_a, [item(shop["rice"])])).json()["promotion_discount"] == "0.00"

    def test_a_new_customer_offer_uses_the_shops_own_history(self, client_a, shop):
        live(client_a, name="Welcome", audience="NEW_CUSTOMER", percent="20")
        fresh_customer = make_customer(client_a, "Fresh")
        cart = [item(shop["rice"], "2")]
        assert calc(client_a, cart, customer_id=fresh_customer["id"])["promotion_discount"] == "20.00"
        anonymous = calc(client_a, cart)
        assert (
            anonymous["promotion_discount"] == "0.00" and anonymous["not_applied"] == []
        )  # an automatic offer is silent
        first = sold(client_a, cart, header={"customer_id": fresh_customer["id"]})
        assert first["promotion_discount"] == "20.00"
        assert calc(client_a, cart, customer_id=fresh_customer["id"])["promotion_discount"] == "0.00"

    def test_a_voided_first_order_does_not_count_as_history(self, client_a, shop):
        live(client_a, audience="NEW_CUSTOMER", percent="20")
        customer = make_customer(client_a)
        first = sold(client_a, [item(shop["rice"])], header={"customer_id": customer["id"]})
        client_a.post(f"{SALES}/{first['id']}/void", json={"reason": "Wrong"})
        assert (
            calc(client_a, [item(shop["rice"])], customer_id=customer["id"])["promotion_discount"] == "10.00"
        )

    def test_a_quick_sale_counts_as_a_first_order(self, client_a, shop):
        live(client_a, audience="NEW_CUSTOMER", percent="20")
        customer = make_customer(client_a)
        quick = client_a.post(
            "/api/v1/quick-sales", json={"gross_amount": "100", "customer_id": customer["id"]}
        ).json()
        client_a.post(f"/api/v1/quick-sales/{quick['id']}/post", json={"payment_method": "CASH"})
        assert (
            calc(client_a, [item(shop["rice"])], customer_id=customer["id"])["promotion_discount"] == "0.00"
        )

    def test_another_shops_history_is_not_ours(self, client_a, client_b, shop):
        live(client_a, audience="NEW_CUSTOMER", percent="20")
        mine = make_customer(client_a, "Mine")
        make_customer(client_b, "Theirs")  # same ids may exist in the other shop; only ours counts
        assert calc(client_a, [item(shop["rice"])], customer_id=mine["id"])["promotion_discount"] == "10.00"

    def test_a_customer_specific_offer(self, client_a, shop):
        vip, other = make_customer(client_a, "VIP"), make_customer(client_a, "Other")
        live(client_a, name="VIP only", audience="CUSTOMERS", customer_ids=[vip["id"]], percent="25")
        cart = [item(shop["rice"], "2")]
        assert calc(client_a, cart, customer_id=vip["id"])["promotion_discount"] == "25.00"
        assert calc(client_a, cart, customer_id=other["id"])["promotion_discount"] == "0.00"
        assert calc(client_a, cart)["promotion_discount"] == "0.00"

    def test_a_coupon_for_a_customer_explains_why_it_does_not_apply(self, client_a, shop):
        vip, other = make_customer(client_a, "VIP"), make_customer(client_a, "Other")
        live(client_a, audience="CUSTOMERS", customer_ids=[vip["id"]], percent="25", coupon_code="VIPONLY")
        no_one = calc(client_a, [item(shop["rice"])], coupon_code="VIPONLY")
        assert "Choose a customer" in no_one["coupon"]["message"]
        wrong = calc(client_a, [item(shop["rice"])], coupon_code="VIPONLY", customer_id=other["id"])
        assert wrong["coupon"]["message"] == "This offer is not available to this customer."

    def test_an_unknown_customer_in_a_preview_is_reported_not_trusted(self, client_a, shop):
        vip = make_customer(client_a, "VIP")
        live(client_a, audience="CUSTOMERS", customer_ids=[vip["id"]], percent="25")
        preview = calc(client_a, [item(shop["rice"])], customer_id=999999)
        assert preview["errors"][0]["field"] == "customer_id" and preview["promotion_discount"] == "0.00"


class TestHistoryNeverChanges:
    def test_editing_pausing_or_expiring_an_offer_leaves_posted_invoices_alone(self, client_a, shop):
        made = live(client_a, name="Festival Offer", percent="10", coupon_code="SAVE10")
        sale = sold(client_a, [item(shop["rice"], "2")], header={"coupon_code": "SAVE10"})
        frozen = sale_detail(client_a, sale)

        client_a.patch(
            f"{API}/{made['id']}", json={"name": "Renamed", "percent": "50", "coupon_code": "NEWCODE"}
        )
        assert sale_detail(client_a, sale) == frozen
        state(client_a, made, "pause")
        assert sale_detail(client_a, sale) == frozen
        state(client_a, made, "expire")
        detail = sale_detail(client_a, sale)
        assert detail == frozen
        assert (
            detail["promotions"][0]["name"] == "Festival Offer"
            and detail["promotions"][0]["terms"] == "10% off"
        )
        assert detail["promotions"][0]["coupon_code"] == "SAVE10" and detail["total_amount"] == "90.00"

    def test_usage_reports_read_the_snapshot_not_the_current_definition(self, client_a, shop):
        made = live(client_a, name="Festival Offer", percent="10")
        sold(client_a, [item(shop["rice"], "2")])
        client_a.patch(f"{API}/{made['id']}", json={"name": "Renamed", "percent": "50"})
        [row] = client_a.get(f"{API}/usage").json()["items"]
        assert (row["name"], row["terms"], row["discount_amount"]) == ("Festival Offer", "10% off", "10.00")
        view = client_a.get(f"{API}/{made['id']}").json()
        assert view["used_count"] == 1 and view["discount_given"] == "10.00" and view["terms"] == "50% off"

    def test_usage_lists_filter_and_show_void_uses_as_void(self, client_a, shop):
        one = live(client_a, name="One", percent="10", coupon_code="ONE", stackable=True, priority=2)
        two = live(client_a, name="Two", percent="10", stackable=True, priority=1)
        a = sold(client_a, [item(shop["rice"], "2")], header={"coupon_code": "ONE"})
        client_a.post(f"{SALES}/{a['id']}/void", json={"reason": "x"})
        sold(client_a, [item(shop["rice"], "2")])
        rows = client_a.get(f"{API}/usage").json()["items"]
        assert sorted((r["name"], r["sale_status"]) for r in rows) == [
            ("One", "VOID"),
            ("Two", "POSTED"),
            ("Two", "VOID"),
        ]
        assert len(client_a.get(f"{API}/usage", params={"promotion_id": one["id"]}).json()["items"]) == 1
        assert len(client_a.get(f"{API}/usage", params={"promotion_id": two["id"]}).json()["items"]) == 2
        assert [
            r["name"] for r in client_a.get(f"{API}/usage", params={"coupon_only": "true"}).json()["items"]
        ] == ["One"]
        assert client_a.get(f"{API}/usage", params={"date_from": "2030-01-01"}).json()["items"] == []
        assert (
            client_a.get(
                f"{API}/usage", params={"date_from": "2030-01-02", "date_to": "2030-01-01"}
            ).status_code
            == 422
        )
        assert client_a.get(f"{API}/{one['id']}").json()["used_count"] == 0  # the voided use is given back


class TestIsolation:
    def test_another_shops_promotion_is_not_found_everywhere(self, client_a, client_b):
        made = live(client_a)
        url = f"{API}/{made['id']}"
        assert client_b.get(url).status_code == 404
        assert client_b.patch(url, json={"name": "x"}).status_code == 404
        for action in ("activate", "pause", "expire"):
            assert client_b.post(f"{url}/{action}").status_code == 404
        assert client_b.get(API).json()["total"] == 0 and client_b.get(f"{API}/usage").json()["items"] == []

    def test_another_shops_offers_never_apply_to_our_bills(self, client_a, client_b, shop):
        live(client_b, percent="90")
        assert calc(client_a, [item(shop["rice"], "2")])["promotion_discount"] == "0.00"

    def test_usage_counts_are_per_shop(self, client_a, client_b, shop, tenant_b, units):
        live(client_a, percent="10", coupon_code="SAME", usage_limit=1)
        live(client_b, percent="10", coupon_code="SAME", usage_limit=1)
        sold(client_a, [item(shop["rice"])], header={"coupon_code": "SAME"})
        supplier_b = make_supplier(client_b)
        product_b = make_product(client_b, tenant_b, units, "RICEB", selling_price="50")
        stock_up(client_b, supplier_b, product_b, 5, 20)
        assert calc(client_b, [item(product_b)], coupon_code="SAME")["coupon"]["applied"] is True

    def test_a_promotion_cannot_name_another_shops_product_category_or_customer(
        self, client_a, client_b, shop, tenant_b, units
    ):
        theirs = make_product(client_b, tenant_b, units, "THEIRS")
        their_customer = make_customer(client_b, "Theirs")
        assert (
            client_a.post(
                API,
                json={
                    "name": "x",
                    "promo_type": "PERCENT",
                    "percent": "5",
                    "scope": "PRODUCTS",
                    "product_ids": [theirs["id"] + 1000],
                },
            ).status_code
            == 422
        )
        assert (
            client_a.post(
                API,
                json={
                    "name": "x",
                    "promo_type": "PERCENT",
                    "percent": "5",
                    "scope": "CATEGORIES",
                    "category_ids": [tenant_b.category.id],
                },
            ).status_code
            == 422
        )
        response = client_a.post(
            API,
            json={
                "name": "x",
                "promo_type": "PERCENT",
                "percent": "5",
                "audience": "CUSTOMERS",
                "customer_ids": [their_customer["id"]],
            },
        )
        assert (
            response.status_code == 422
            or client_a.get(f"{CUSTOMERS}/{their_customer['id']}").status_code == 404
        )


class TestDatabaseRules:
    """The database refuses what the service would never write: a second line of defence."""

    def promotion(self, tenant, **over):
        base = dict(shop_id=tenant.shop.id, name="P", promo_type=PromotionType.PERCENT, scope=PromotionScope.CART,
                    status=PromotionStatus.DRAFT, percent_bp=1000, created_by=tenant.user.id, targets={})  # fmt: skip
        return Promotion(**{**base, **over})

    def test_a_percent_offer_needs_a_percentage_and_nothing_else(self, session, tenant_a):
        assert_rejected(session, self.promotion(tenant_a, percent_bp=None), match="benefit_matches_type")
        assert_rejected(session, self.promotion(tenant_a, amount=D("5")), match="benefit_matches_type")
        assert_rejected(session, self.promotion(tenant_a, percent_bp=0), match="percent_bp_range")
        assert_rejected(session, self.promotion(tenant_a, percent_bp=10001), match="percent_bp_range")

    def test_an_offer_price_or_buy_x_get_y_needs_chosen_items(self, session, tenant_a):
        bad = self.promotion(
            tenant_a, promo_type=PromotionType.OFFER_PRICE, percent_bp=None, offer_price=D("9")
        )
        assert_rejected(session, bad, match="type_needs_items")

    def test_the_date_window_must_be_ordered(self, session, tenant_a):
        now = datetime.now(UTC)
        assert_rejected(
            session, self.promotion(tenant_a, starts_at=now, ends_at=now), match="window_is_ordered"
        )

    def test_limits_must_be_positive(self, session, tenant_a):
        assert_rejected(session, self.promotion(tenant_a, usage_limit=0), match="usage_limit_positive")
        assert_rejected(session, self.promotion(tenant_a, max_discount=D("0")), match="max_discount_positive")

    def test_a_coupon_code_is_unique_per_shop_only(self, session, tenant_a, tenant_b):
        session.add(self.promotion(tenant_a, coupon_code="X1"))
        session.add(self.promotion(tenant_b, coupon_code="X1"))
        session.flush()
        assert_rejected(session, self.promotion(tenant_a, coupon_code="X1"), match="UNIQUE")
        assert_rejected(session, self.promotion(tenant_a, coupon_code="  "), match="not_blank")

    def test_an_offer_cannot_be_created_by_another_shops_user(self, session, tenant_a, tenant_b):
        assert_rejected(session, self.promotion(tenant_a, created_by=tenant_b.user.id), match="FOREIGN KEY")

    def test_a_sale_total_must_equal_subtotal_less_both_discounts(self, session, tenant_a):
        def sale(**over):
            base = dict(shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=today_in_shop_timezone(),
                        subtotal=D("100"), discount=D("10"), promotion_discount=D("20"), total_amount=D("70"),
                        created_by=tenant_a.user.id)  # fmt: skip
            return Sale(**{**base, **over})

        assert_rejected(session, sale(total_amount=D("90")), match="total_is_subtotal_less_discounts")
        assert_rejected(
            session, sale(promotion_discount=D("-1"), total_amount=D("91")), match="promotion_discount"
        )
        session.add(sale())
        session.flush()  # the correct one is accepted

    def test_a_lines_share_cannot_exceed_the_line(self, session, tenant_a, units):
        product = factories.make_product(session, tenant_a.shop, tenant_a.category)
        sale = Sale(shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=today_in_shop_timezone(),
                    created_by=tenant_a.user.id)  # fmt: skip
        session.add(sale)
        session.flush()
        line = SaleItem(shop_id=tenant_a.shop.id, sale_id=sale.id, product_id=product.id, unit_id=product.unit_id,
                        quantity=D("1"), unit_price=D("10"), line_total=D("10"), promotion_discount=D("10.01"))  # fmt: skip
        assert_rejected(session, line, match="promotion_within_line")

    def test_a_snapshot_cannot_point_at_another_shops_sale_or_promotion(self, session, tenant_a, tenant_b):
        theirs = self.promotion(tenant_b)
        session.add(theirs)
        sale = Sale(shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=today_in_shop_timezone(),
                    created_by=tenant_a.user.id)  # fmt: skip
        session.add(sale)
        session.flush()
        row = SalePromotion(shop_id=tenant_a.shop.id, sale_id=sale.id, promotion_id=theirs.id, position=1, name="n",
                            promo_type=PromotionType.PERCENT, terms="t", discount_amount=D("1"), basis="b")  # fmt: skip
        assert_rejected(session, row, match="FOREIGN KEY")

    def test_a_snapshot_must_have_a_positive_amount_and_a_name(self, session, tenant_a):
        promotion = self.promotion(tenant_a)
        session.add(promotion)
        sale = Sale(shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=today_in_shop_timezone(),
                    created_by=tenant_a.user.id)  # fmt: skip
        session.add(sale)
        session.flush()

        def row(**over):
            base = dict(shop_id=tenant_a.shop.id, sale_id=sale.id, promotion_id=promotion.id, position=1, name="n",
                        promo_type=PromotionType.PERCENT, terms="t", discount_amount=D("1"), basis="b")  # fmt: skip
            return SalePromotion(**{**base, **over})

        assert_rejected(session, row(discount_amount=D("0")), match="discount_amount_positive")
        assert_rejected(session, row(name=" "), match="not_blank")


class TestEveryBusinessType:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_offers_work_for_every_business_type(
        self, make_client, tenant_of, give_plan, business_type, units
    ):
        tenant = tenant_of(business_type)
        give_plan(tenant, "pro")
        client = make_client(tenant)
        supplier = make_supplier(client)
        product = make_product(client, tenant, units, "ITEM", selling_price="200")
        stock_up(client, supplier, product, 5, 100)
        live(client, percent="10")
        sale = sold(client, [item(product, "2")])
        assert sale["promotion_discount"] == "40.00" and sale["total_amount"] == "360.00"


class TestQuickSalesAndOffers:
    def test_offers_never_touch_quick_sales(self, client_a, fresh):
        live(client_a, percent="50")
        quick = client_a.post("/api/v1/quick-sales", json={"gross_amount": "100"}).json()
        posted = client_a.post(
            f"/api/v1/quick-sales/{quick['id']}/post", json={"payment_method": "CASH"}
        ).json()
        assert posted["total_amount"] == "100.00" and posted["discount"] == "0.00"
        assert (
            client_a.post("/api/v1/quick-sales", json={"gross_amount": "5", "coupon_code": "X"}).status_code
            == 422
        )
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(SalePromotion))) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(QuickSale))) == 1


class TestMigration0009:
    NOW = "'2026-09-01 10:00:00.000000'"

    def seed_at_0008(self, url):
        from alembic import command
        from sqlalchemy import text

        from app.db.engine import create_db_engine
        from tests.conftest import alembic_config

        command.upgrade(alembic_config(url), "0008")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES (7, 'S', 'BAKERY', '9', 'x', 'WARN', {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (3, 7, 'o@x.l', '!', 'O', 'OWNER', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO sales (id, shop_id, invoice_no, status, sale_date, subtotal, discount, total_amount, payment_type, amount_paid, payment_method, created_by, posted_at, posted_by, created_at, updated_at) VALUES (5, 7, 'INV/1', 'POSTED', '2026-08-30', 100000, 10000, 90000, 'PAID', 90000, 'CASH', 3, {self.NOW}, 3, {self.NOW}, {self.NOW})"
                )
            )
        engine.dispose()

    def test_existing_sales_keep_their_totals_and_get_no_promotion(self, tmp_path):
        from alembic import command
        from sqlalchemy import text

        from app.db.engine import create_db_engine
        from tests.conftest import alembic_config, sqlite_url

        url = sqlite_url(tmp_path / "m.db")
        self.seed_at_0008(url)

        command.upgrade(alembic_config(url), "head")

        engine = create_db_engine(url)
        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT subtotal, discount, promotion_discount, total_amount, coupon_code FROM sales WHERE id = 5"
                )
            ).one()
            assert tuple(row) == (100000, 10000, 0, 90000, None)
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            tables = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert {"promotions", "sale_promotions"} <= tables
        engine.dispose()
        command.check(alembic_config(url))

    def test_the_new_total_rule_is_enforced_on_the_migrated_database(self, tmp_path):
        from alembic import command
        from sqlalchemy import text

        from app.db.engine import create_db_engine
        from tests.conftest import alembic_config, sqlite_url

        url = sqlite_url(tmp_path / "m.db")
        self.seed_at_0008(url)
        command.upgrade(alembic_config(url), "head")
        insert = (
            "INSERT INTO sales (shop_id, sale_date, subtotal, discount, promotion_discount, total_amount, created_by,"
            " status, created_at, updated_at) VALUES (7, '2026-09-01', 10000, 1000, :promo, :total, 3, 'DRAFT',"
            f" {self.NOW}, {self.NOW})"
        )
        engine = create_db_engine(url)
        with engine.connect() as c:
            with pytest.raises(Exception, match="total_is_subtotal_less_discounts"):
                c.execute(text(insert), {"promo": 2000, "total": 8000})  # forgot the promotion in the total
            c.rollback()
            c.execute(text(insert), {"promo": 2000, "total": 7000})  # subtotal - discount - promotion
            c.rollback()
        engine.dispose()

    def test_downgrade_is_refused_while_a_sale_used_a_promotion_and_works_after_that_is_gone(self, tmp_path):
        from alembic import command
        from sqlalchemy import text

        from app.db.engine import create_db_engine
        from tests.conftest import alembic_config, sqlite_url

        url = sqlite_url(tmp_path / "m.db")
        self.seed_at_0008(url)
        config = alembic_config(url)
        command.upgrade(config, "head")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE sales SET promotion_discount = 5000, total_amount = 85000, amount_paid = 85000 WHERE id = 5"
                )
            )
        engine.dispose()

        with pytest.raises(RuntimeError, match="promotion discount"):
            command.downgrade(config, "0008")

        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE sales SET promotion_discount = 0, total_amount = 90000, amount_paid = 90000 WHERE id = 5"
                )
            )
        engine.dispose()
        command.downgrade(config, "0008")
        command.upgrade(config, "head")
        command.check(config)
