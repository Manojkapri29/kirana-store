"""Plans, entitlements, limits and usage: the subscription foundation. No payment exists, and none is faked."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Plan, PlanFeature, ShopSubscription, SubscriptionUsage
from app.models.enums import BillingInterval, SubscriptionStatus
from app.services import entitlement_service as plans
from app.services.errors import ConflictError, EntitlementError, InvalidInputError, NotFoundError
from tests.conftest import assert_rejected
from tests.test_purchases_api import make_product, make_supplier
from tests.test_sales_api import API as SALES
from tests.test_sales_api import item, sold, stock_up

SUBSCRIPTION = "/api/v1/subscription"
PRODUCTS = "/api/v1/products"


def entitlements(session_factory, tenant, **kwargs):
    with session_factory() as s:
        return plans.get_entitlements(s, tenant.shop.id, **kwargs)


class TestTheSeededPlans:
    def test_free_basic_and_pro_exist_as_data(self, session_factory):
        with session_factory() as s:
            views = {v.plan.code: v for v in plans.list_plans(s)}

        assert list(views) == ["free", "basic", "pro"]
        assert views["free"].plan.price == Decimal("0.00")
        assert views["basic"].plan.price is None and views["pro"].plan.price is None  # no price is invented
        assert all(
            v.plan.currency == "INR" and v.plan.billing_interval is BillingInterval.MONTHLY
            for v in views.values()
        )

    def test_every_key_the_code_checks_is_present_on_every_seeded_plan(self, session_factory):
        with session_factory() as s:
            for view in plans.list_plans(s):
                assert set(plans.FEATURES) <= set(view.features), view.plan.code
                assert set(plans.LIMITS) <= set(view.limits), view.plan.code

    def test_the_plans_differ_as_designed(self, session_factory):
        with session_factory() as s:
            views = {v.plan.code: v for v in plans.list_plans(s)}
        assert not any(views["free"].features[k] for k in plans.FEATURES)
        assert views["basic"].features["promotions"] and not views["basic"].features["price_intelligence"]
        assert all(views["pro"].features[k] for k in plans.FEATURES)
        assert views["pro"].limits["max_products"] is None  # unlimited


class TestWhichPlanAShopIsOn:
    def test_a_shop_with_no_subscription_is_on_the_default_free_plan(self, session_factory, tenant_a):
        e = entitlements(session_factory, tenant_a)
        assert (e.plan_code, e.source, e.status) == ("free", "default", None)
        assert not e.allows("promotions") and e.limit("max_products") == 100

    def test_assigning_a_plan_changes_what_is_allowed(self, session_factory, tenant_a, give_plan):
        give_plan(tenant_a, "pro")
        e = entitlements(session_factory, tenant_a)
        assert (e.plan_code, e.source, e.status) == ("pro", "subscription", "ACTIVE")
        assert e.allows("price_intelligence") and e.limit("max_products") is None

    def test_an_unknown_feature_is_not_allowed_and_an_unknown_limit_is_unlimited(
        self, session_factory, tenant_a
    ):
        e = entitlements(session_factory, tenant_a)
        assert e.allows("does_not_exist") is False and e.limit("does_not_exist") is None

    def test_an_expired_subscription_falls_back_to_the_default_plan(
        self, session_factory, tenant_a, give_plan
    ):
        past = datetime.now(UTC) - timedelta(days=30)
        give_plan(tenant_a, "pro", starts_at=past, ends_at=past + timedelta(days=10))
        e = entitlements(session_factory, tenant_a)
        assert (e.plan_code, e.source) == ("free", "default") and not e.allows("promotions")

    def test_a_subscription_that_has_not_started_gives_nothing_yet(
        self, session_factory, tenant_a, give_plan
    ):
        future = datetime.now(UTC) + timedelta(days=5)
        give_plan(tenant_a, "pro", starts_at=future)
        assert entitlements(session_factory, tenant_a).plan_code == "free"
        assert entitlements(session_factory, tenant_a, now=future + timedelta(days=1)).plan_code == "pro"

    def test_a_trial_counts_until_it_ends(self, session_factory, tenant_a, give_plan):
        give_plan(
            tenant_a, "basic", status=SubscriptionStatus.TRIAL, ends_at=datetime.now(UTC) + timedelta(days=14)
        )
        e = entitlements(session_factory, tenant_a)
        assert (e.plan_code, e.status) == ("basic", "TRIAL") and e.allows("promotions")
        later = entitlements(session_factory, tenant_a, now=datetime.now(UTC) + timedelta(days=15))
        assert later.plan_code == "free"

    def test_an_inactive_plan_gives_nothing(self, session_factory, tenant_a, give_plan):
        give_plan(tenant_a, "pro")
        with session_factory() as s, s.begin():
            plans.get_plan(s, "pro").is_active = False
        assert entitlements(session_factory, tenant_a).plan_code == "free"

    def test_changing_plan_keeps_history_and_leaves_one_current(self, session_factory, tenant_a, give_plan):
        give_plan(tenant_a, "basic")
        give_plan(tenant_a, "pro")

        with session_factory() as s:
            rows = s.scalars(select(ShopSubscription).order_by(ShopSubscription.id)).all()

        assert [(r.plan_id, r.status.value) for r in rows] == [(2, "CANCELLED"), (3, "ACTIVE")]
        assert entitlements(session_factory, tenant_a).plan_code == "pro"

    def test_the_database_itself_allows_only_one_current_subscription(self, session, tenant_a, give_plan):
        give_plan(tenant_a, "pro")
        duplicate = ShopSubscription(
            shop_id=tenant_a.shop.id, plan_id=2, status=SubscriptionStatus.ACTIVE, starts_at=datetime.now(UTC)
        )
        assert_rejected(session, duplicate, match="UNIQUE")

    def test_history_rows_are_not_limited(self, session, tenant_a):
        now = datetime.now(UTC)
        for status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED, SubscriptionStatus.EXPIRED):
            session.add(ShopSubscription(shop_id=tenant_a.shop.id, plan_id=1, status=status, starts_at=now))
        session.commit()

    def test_a_subscription_cannot_point_at_nothing(self, session, tenant_a):
        bad = ShopSubscription(
            shop_id=tenant_a.shop.id,
            plan_id=999,
            status=SubscriptionStatus.ACTIVE,
            starts_at=datetime.now(UTC),
        )
        assert_rejected(session, bad, match="FOREIGN KEY")


class TestManagingPlans:
    def test_creating_a_plan_with_features_and_limits(self, session_factory):
        with session_factory() as s, s.begin():
            plans.create_plan(
                s,
                code=" Starter ",
                name="Starter",
                price=Decimal("199.00"),
                billing_interval=BillingInterval.YEARLY,
                sort_order=9,
            )
            view = plans.set_plan_entries(
                s, "starter", features={"promotions": True}, limits={"max_products": 250, "max_users": None}
            )

        assert view.plan.code == "starter" and view.plan.billing_interval is BillingInterval.YEARLY
        assert view.features == {"promotions": True} and view.limits == {
            "max_products": 250,
            "max_users": None,
        }

    def test_a_plan_price_is_data_not_code(self, session_factory):
        with session_factory() as s, s.begin():
            plans.get_plan(s, "basic").price = Decimal("499.00")
        with session_factory() as s:
            assert [v.plan.price for v in plans.list_plans(s) if v.plan.code == "basic"] == [
                Decimal("499.00")
            ]

    def test_bad_plans_are_refused(self, session_factory):
        with session_factory() as s:
            with pytest.raises(ConflictError):
                plans.create_plan(s, code="FREE", name="Again")
            with pytest.raises(InvalidInputError):
                plans.create_plan(s, code="x", name="  ")
            with pytest.raises(InvalidInputError):
                plans.create_plan(s, code="neg", name="Neg", price=Decimal("-1"))
            with pytest.raises(InvalidInputError):
                plans.set_plan_entries(s, "free", limits={"max_products": -5})
            with pytest.raises(NotFoundError):
                plans.get_plan(s, "nope")

    def test_bad_assignments_are_refused(self, session_factory, tenant_a):
        past = datetime.now(UTC) - timedelta(days=1)
        with session_factory() as s:
            with pytest.raises(NotFoundError):
                plans.assign_plan(s, 99999, "pro")
            with pytest.raises(NotFoundError):
                plans.assign_plan(s, tenant_a.shop.id, "nope")
            with pytest.raises(InvalidInputError):
                plans.assign_plan(s, tenant_a.shop.id, "pro", status=SubscriptionStatus.EXPIRED)
            with pytest.raises(InvalidInputError):
                plans.assign_plan(s, tenant_a.shop.id, "pro", ends_at=past)

    def test_a_limit_of_zero_means_none_allowed_and_none_means_unlimited(self, session_factory, tenant_a):
        with session_factory() as s, s.begin():
            plans.set_plan_entries(s, "free", limits={"max_products": 0, "max_users": None})
        e = entitlements(session_factory, tenant_a)
        assert e.limit("max_products") == 0 and e.limit("max_users") is None

    def test_a_disabled_limit_row_means_zero(self, session_factory, tenant_a):
        with session_factory() as s, s.begin():
            row = s.scalars(
                select(PlanFeature)
                .join(Plan)
                .where(Plan.code == "free", PlanFeature.feature_key == "max_products")
            ).one()
            row.enabled = False
        assert entitlements(session_factory, tenant_a).limit("max_products") == 0

    def test_there_is_no_endpoint_that_changes_a_plan(self, client_a):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client_a, method)(SUBSCRIPTION).status_code == 405
        assert client_a.post(f"{SUBSCRIPTION}/upgrade").status_code == 404


class TestCheckingFeaturesAndLimits:
    def test_require_feature_passes_or_raises_a_403_error(self, session_factory, tenant_a, give_plan):
        with session_factory() as s, pytest.raises(EntitlementError) as info:
            plans.require_feature(s, tenant_a.shop.id, "promotions")
        assert info.value.feature == "promotions" and "Free plan" in info.value.message
        give_plan(tenant_a, "basic")
        with session_factory() as s:
            assert plans.require_feature(s, tenant_a.shop.id, "promotions").plan_code == "basic"

    def test_check_limit_counts_what_is_being_added(self, session_factory, tenant_a):
        with session_factory() as s:
            plans.check_limit(s, tenant_a.shop.id, "max_users", 1)  # 2nd of 2: fine
            with pytest.raises(EntitlementError, match="allows 2 users"):
                plans.check_limit(s, tenant_a.shop.id, "max_users", 2)
            with pytest.raises(EntitlementError):
                plans.check_limit(s, tenant_a.shop.id, "max_users", 1, adding=2)

    def test_an_unlimited_plan_never_refuses(self, session_factory, tenant_a, give_plan):
        give_plan(tenant_a, "pro")
        with session_factory() as s:
            plans.check_limit(s, tenant_a.shop.id, "max_products", 10**9)

    def test_the_users_limit_uses_the_real_user_count(self, session_factory, tenant_a):
        with session_factory() as s:
            assert plans.user_count(s, tenant_a.shop.id) == 1

    def test_the_api_error_names_the_feature_so_a_screen_can_offer_an_upgrade(
        self, client_a, tenant_a, set_plan_limit
    ):
        set_plan_limit("free", max_products=0)
        response = client_a.post(
            PRODUCTS,
            json={
                "sku": "A",
                "name": "A",
                "category_id": tenant_a.category.id,
                "unit_id": 1,
                "selling_price": "1",
            },
        )
        item_ = response.json()["detail"][0]
        assert (
            response.status_code == 403
            and item_["type"] == "plan_limit"
            and item_["feature"] == "max_products"
        )


@pytest.fixture
def set_plan_limit(session_factory):
    def _set(code: str, **limits: int | None) -> None:
        with session_factory() as s, s.begin():
            plans.set_plan_entries(s, code, limits=limits)

    return _set


class TestTheProductLimitIsEnforcedByTheBackend:
    def body(self, tenant, units, sku):
        return {
            "sku": sku,
            "name": sku,
            "category_id": tenant.category.id,
            "unit_id": units["pcs"],
            "selling_price": "10",
        }

    def test_creating_a_product_over_the_limit_is_refused(
        self, client_a, tenant_a, units, set_plan_limit, fresh
    ):
        set_plan_limit("free", max_products=2)
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "A")).status_code == 201
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "B")).status_code == 201

        refused = client_a.post(PRODUCTS, json=self.body(tenant_a, units, "C"))

        assert refused.status_code == 403 and "allows 2 products" in refused.text
        from app.models import Product

        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Product))) == 2

    def test_a_deactivated_product_frees_a_place_and_reactivating_is_checked(
        self, client_a, tenant_a, units, set_plan_limit
    ):
        set_plan_limit("free", max_products=1)
        first = client_a.post(PRODUCTS, json=self.body(tenant_a, units, "A")).json()["product"]
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "B")).status_code == 403

        client_a.post(f"{PRODUCTS}/{first['id']}/deactivate")
        second = client_a.post(PRODUCTS, json=self.body(tenant_a, units, "B"))
        assert second.status_code == 201

        assert (
            client_a.post(f"{PRODUCTS}/{first['id']}/activate").status_code == 403
        )  # would be the 2nd active one

    def test_a_bigger_plan_lifts_the_limit(self, client_a, tenant_a, units, set_plan_limit, give_plan):
        set_plan_limit("free", max_products=1)
        client_a.post(PRODUCTS, json=self.body(tenant_a, units, "A"))
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "B")).status_code == 403
        give_plan(tenant_a, "pro")
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "B")).status_code == 201

    def test_editing_an_existing_product_is_never_blocked_by_the_limit(
        self, client_a, tenant_a, units, set_plan_limit
    ):
        set_plan_limit("free", max_products=1)
        product = client_a.post(PRODUCTS, json=self.body(tenant_a, units, "A")).json()["product"]
        assert client_a.patch(f"{PRODUCTS}/{product['id']}", json={"name": "Renamed"}).status_code == 200

    def test_another_shops_products_do_not_count(
        self, client_a, client_b, tenant_a, tenant_b, units, set_plan_limit
    ):
        set_plan_limit("free", max_products=1)
        client_b.post(PRODUCTS, json=self.body(tenant_b, units, "B1"))
        assert client_a.post(PRODUCTS, json=self.body(tenant_a, units, "A1")).status_code == 201


class TestMonthlyInvoicesAreMetered:
    @pytest.fixture
    def stocked(self, client_a, tenant_a, units):
        supplier = make_supplier(client_a)
        rice = make_product(client_a, tenant_a, units, "RICE", selling_price="50")
        stock_up(client_a, supplier, rice, 50, 20)
        return rice

    def test_posting_counts_and_the_limit_stops_the_next_one(
        self, client_a, stocked, set_plan_limit, fresh, tenant_a
    ):
        set_plan_limit("free", max_monthly_invoices=2)
        sold(client_a, [item(stocked)])
        sold(client_a, [item(stocked)])
        third = client_a.post(SALES, json={"items": [item(stocked)]}).json()

        refused = client_a.post(f"{SALES}/{third['id']}/post", json={"payment_method": "CASH"})

        assert refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "max_monthly_invoices"
        assert (
            client_a.get(f"{SALES}/{third['id']}").json()["status"] == "DRAFT"
        )  # still a draft, nothing consumed
        assert client_a.get(f"/api/v1/inventory/products/{stocked['id']}").json()["current_stock"] == "48.000"
        with_usage = client_a.get(SUBSCRIPTION).json()["usage"]
        assert with_usage["invoices"] == 2

    def test_a_refused_post_takes_no_number(self, client_a, stocked, set_plan_limit):
        set_plan_limit("free", max_monthly_invoices=1)
        sold(client_a, [item(stocked)])
        blocked = client_a.post(SALES, json={"items": [item(stocked)]}).json()
        assert (
            client_a.post(f"{SALES}/{blocked['id']}/post", json={"payment_method": "CASH"}).status_code == 403
        )

        # after the limit is lifted the same draft posts as number 0002, not 0003
        set_plan_limit("free", max_monthly_invoices=None)
        posted = client_a.post(f"{SALES}/{blocked['id']}/post", json={"payment_method": "CASH"})
        assert posted.status_code == 200 and posted.json()["invoice_no"].endswith("/0002")

    def test_voiding_does_not_give_the_allowance_back(self, client_a, stocked, set_plan_limit):
        set_plan_limit("free", max_monthly_invoices=1)
        first = sold(client_a, [item(stocked)])
        client_a.post(f"{SALES}/{first['id']}/void", json={"reason": "x"})
        again = client_a.post(SALES, json={"items": [item(stocked)]}).json()
        assert (
            client_a.post(f"{SALES}/{again['id']}/post", json={"payment_method": "CASH"}).status_code == 403
        )

    def test_each_shop_has_its_own_count(self, client_a, client_b, tenant_b, units, stocked, set_plan_limit):
        set_plan_limit("free", max_monthly_invoices=1)
        sold(client_a, [item(stocked)])
        supplier = make_supplier(client_b, "B")
        theirs = make_product(client_b, tenant_b, units, "RICE", selling_price="50")
        stock_up(client_b, supplier, theirs, 5, 20)
        assert sold(client_b, [item(theirs)])["status"] == "POSTED"

    def test_a_plan_with_no_limit_never_stops_billing(self, client_a, stocked, give_plan, tenant_a):
        give_plan(tenant_a, "pro")
        for _ in range(5):
            sold(client_a, [item(stocked)])
        assert client_a.get(SUBSCRIPTION).json()["usage"]["invoices"] == 5


class TestUsageCounters:
    def test_the_period_follows_the_shops_own_timezone(self, session_factory, tenant_a):
        # 20:00 UTC on 31 August is already 1 September in India (UTC+5:30)
        moment = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
        with session_factory() as s:
            assert plans.current_period(s, tenant_a.shop.id, moment) == "2026-09"
            assert (
                plans.current_period(s, tenant_a.shop.id, datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
                == "2026-08"
            )

    def test_counters_are_per_shop_month_and_metric(self, session_factory, tenant_a, tenant_b):
        with session_factory() as s, s.begin():
            plans.use_metered(s, tenant_a.shop.id, "invoices")
            plans.use_metered(s, tenant_a.shop.id, "invoices")
            plans.use_metered(s, tenant_a.shop.id, "exports")  # a metric with no plan limit is only counted
            plans.use_metered(s, tenant_b.shop.id, "invoices")
        with session_factory() as s:
            assert plans.get_usage(s, tenant_a.shop.id, "invoices") == 2
            assert plans.get_usage(s, tenant_a.shop.id, "exports") == 1
            assert plans.get_usage(s, tenant_b.shop.id, "invoices") == 1
            assert plans.get_usage(s, tenant_a.shop.id, "invoices", "2020-01") == 0

    def test_a_rolled_back_transaction_does_not_count(self, session_factory, tenant_a):
        with pytest.raises(RuntimeError), session_factory() as s, s.begin():
            plans.use_metered(s, tenant_a.shop.id, "invoices")
            raise RuntimeError("the action failed")
        with session_factory() as s:
            assert plans.get_usage(s, tenant_a.shop.id, "invoices") == 0
            assert s.scalar(select(func.count()).select_from(SubscriptionUsage)) == 0

    def test_a_metered_limit_of_zero_refuses_the_first_use(self, session_factory, tenant_a):
        with session_factory() as s, pytest.raises(EntitlementError, match="price checks"):
            plans.use_metered(s, tenant_a.shop.id, "price_lookups")  # the Free plan allows none


class TestTheSubscriptionScreenData:
    def test_the_current_plan_features_limits_usage_and_available_plans(self, client_a, give_plan, tenant_a):
        give_plan(tenant_a, "basic")

        data = client_a.get(SUBSCRIPTION).json()

        assert (data["plan_code"], data["source"], data["status"]) == ("basic", "subscription", "ACTIVE")
        assert data["features"]["promotions"] is True and data["features"]["price_intelligence"] is False
        assert data["limits"]["max_products"] == 1000
        assert set(data["usage"]) == {"products", "users", "invoices", "price_lookups"}
        assert [p["code"] for p in data["plans"]] == ["free", "basic", "pro"]
        assert [p["is_current"] for p in data["plans"]] == [False, True, False]
        assert data["plans"][0]["price"] == "0.00" and data["plans"][1]["price"] is None

    def test_a_shop_without_a_subscription_is_shown_as_the_default_plan(self, client_a):
        data = client_a.get(SUBSCRIPTION).json()
        assert (data["plan_code"], data["source"]) == ("free", "default")

    def test_each_shop_sees_only_its_own_plan_and_usage(
        self, client_a, client_b, tenant_a, give_plan, units, tenant_b
    ):
        give_plan(tenant_a, "pro")
        assert client_a.get(SUBSCRIPTION).json()["plan_code"] == "pro"
        assert client_b.get(SUBSCRIPTION).json()["plan_code"] == "free"

    def test_no_secret_or_internal_id_is_in_the_response(self, client_a):
        text = client_a.get(SUBSCRIPTION).text.lower()
        assert "api_key" not in text and "password" not in text and "shop_id" not in text
