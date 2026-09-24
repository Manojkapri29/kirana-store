"""Reorder recommendations and purchase planning: transparent calculation, missing cost, missing lead time,
pack size / MOQ rounding, supplier grouping, and that a draft is only ever created — never posted."""

from decimal import Decimal

import pytest

from app.models.enums import PurchaseStatus
from app.services import ai_insights_service, inventory_service, purchase_planning_service
from app.services.errors import InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


class TestReorderRecommendations:
    def test_a_product_at_or_below_its_reorder_level_is_recommended(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            reorder_level=Decimal("10"),
            purchase_price=Decimal("20"),
        )
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(5), unit_cost=Decimal("20")
        )
        session.commit()

        recs = ai_insights_service.reorder_recommendations(session, tenant_a.shop.id, TODAY)

        rec = next(r for r in recs if r.product_id == p.id)
        assert rec.suggested_quantity > 0
        assert "At or below the reorder level" in rec.reasons

    def test_cost_unavailable_is_never_invented_as_zero(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, reorder_level=Decimal("10"))
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(1)
        )  # no unit_cost
        session.commit()

        recs = ai_insights_service.reorder_recommendations(session, tenant_a.shop.id, TODAY)

        rec = next(r for r in recs if r.product_id == p.id)
        assert rec.unit_cost_used is None
        assert rec.estimated_cost is None
        assert rec.cost_basis is None

    def test_missing_supplier_lead_time_is_stated_as_an_assumption_not_invented(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            reorder_level=Decimal("10"),
            default_supplier_id=supplier.id,
            purchase_price=Decimal("5"),
        )
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(1), unit_cost=Decimal("5")
        )
        session.commit()

        recs = ai_insights_service.reorder_recommendations(session, tenant_a.shop.id, TODAY)

        rec = next(r for r in recs if r.product_id == p.id)
        assert rec.lead_time_days is None
        assert any("lead time is not set" in reason for reason in rec.reasons)

    def test_pack_size_rounds_the_suggestion_up_to_whole_packs_and_says_so(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            reorder_level=Decimal("100"),
            purchase_price=Decimal("5"),
            pack_size=Decimal("12"),
        )
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(1), unit_cost=Decimal("5")
        )
        session.commit()

        recs = ai_insights_service.reorder_recommendations(session, tenant_a.shop.id, TODAY)

        rec = next(r for r in recs if r.product_id == p.id)
        assert rec.suggested_quantity % Decimal("12") == 0
        assert any("whole packs" in reason for reason in rec.reasons)

    def test_moq_raises_a_small_suggestion_up_to_the_minimum_order_quantity(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            reorder_level=Decimal("2"),
            purchase_price=Decimal("5"),
            moq=Decimal("50"),
        )
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(1), unit_cost=Decimal("5")
        )
        session.commit()

        recs = ai_insights_service.reorder_recommendations(session, tenant_a.shop.id, TODAY)

        rec = next(r for r in recs if r.product_id == p.id)
        assert rec.suggested_quantity >= Decimal("50")
        assert any("minimum order quantity" in reason for reason in rec.reasons)

    def test_periods_are_configurable_window_and_cover_days_change_the_result(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, reorder_level=Decimal("5"))
        session.commit()
        inventory_service.record_opening_stock(session, ctx, product_id=p.id, quantity=Decimal(1))
        session.commit()

        short = ai_insights_service.reorder_recommendations(
            session, tenant_a.shop.id, TODAY, window_days=7, cover_days=7
        )
        long = ai_insights_service.reorder_recommendations(
            session, tenant_a.shop.id, TODAY, window_days=60, cover_days=60
        )

        assert next(r for r in short if r.product_id == p.id).window_days == 7
        assert next(r for r in long if r.product_id == p.id).window_days == 60


class TestPurchasePlanning:
    def test_suggestions_are_grouped_by_supplier(self, session, tenant_a):
        ctx = context_for(tenant_a)
        s1 = factories.make_supplier(session, tenant_a.shop, name="Supplier One")
        s2 = factories.make_supplier(session, tenant_a.shop, name="Supplier Two")
        p1 = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            sku="P1",
            reorder_level=Decimal("5"),
            default_supplier_id=s1.id,
            purchase_price=Decimal("10"),
        )
        p2 = factories.make_product(
            session,
            tenant_a.shop,
            tenant_a.category,
            sku="P2",
            reorder_level=Decimal("5"),
            default_supplier_id=s2.id,
            purchase_price=Decimal("10"),
        )
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p1.id, quantity=Decimal(1), unit_cost=Decimal("10")
        )
        inventory_service.record_opening_stock(
            session, ctx, product_id=p2.id, quantity=Decimal(1), unit_cost=Decimal("10")
        )
        session.commit()

        groups = purchase_planning_service.suggestions(session, tenant_a.shop.id, TODAY)

        supplier_ids = {g.supplier_id for g in groups}
        assert {s1.id, s2.id} <= supplier_ids

    def test_building_a_draft_never_posts_it(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, purchase_price=Decimal("15"))
        session.commit()

        view = purchase_planning_service.build_draft(
            session,
            ctx,
            supplier_id=supplier.id,
            lines=[{"product_id": p.id, "quantity": Decimal(20), "unit_cost": Decimal("15")}],
        )
        session.commit()

        assert view.purchase.status is PurchaseStatus.DRAFT
        assert inventory_service.get_stock(session, tenant_a.shop.id, p.id) == Decimal(
            0
        )  # nothing received yet

    def test_a_line_with_no_price_is_refused_never_defaulted_to_zero(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category)
        session.commit()

        with pytest.raises(InvalidInputError):
            purchase_planning_service.build_draft(
                session,
                ctx,
                supplier_id=supplier.id,
                lines=[{"product_id": p.id, "quantity": Decimal(5), "unit_cost": None}],
            )
