"""Stock counting: the full CREATE -> COUNTING -> REVIEW -> APPROVE -> POST lifecycle, cancellation, the
creator/approver separation, the large-variance approval gate, and that posting only ever goes through
`inventory_service.record_adjustment` (never a second stock write)."""

from decimal import Decimal

import pytest

from app.models.enums import AdjustmentReason, StockCountScope, StockCountStatus
from app.services import inventory_service, stock_count_service
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError
from tests import factories
from tests.conftest import context_for


def _product(session, tenant, **overrides):
    return factories.make_product(session, tenant.shop, tenant.category, **overrides)


class TestLifecycle:
    def test_a_count_walks_from_draft_to_posted_and_adjusts_stock_through_inventory_service(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a, avg_cost=Decimal("10"))
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(10), unit_cost=Decimal("10")
        )
        session.commit()

        count = stock_count_service.create(session, ctx, title="Weekly count", scope=StockCountScope.FULL)
        session.commit()
        assert count.status is StockCountStatus.DRAFT

        stock_count_service.start_counting(session, ctx, count.id)
        session.commit()
        stock_count_service.enter_counts(
            session,
            ctx,
            count.id,
            [{"product_id": p.id, "counted_quantity": Decimal(8), "note": "2 damaged"}],
        )
        session.commit()

        reviewed = stock_count_service.submit_for_review(session, ctx, count.id)
        session.commit()
        assert reviewed.status is StockCountStatus.REVIEW
        items = stock_count_service.get_items(session, tenant_a.shop.id, count.id)
        assert items[0].variance == Decimal(-2)

        approver_ctx = context_for(tenant_a)
        # A different approver: create a second user so creator != approver.
        second = factories.make_user(session, tenant_a.shop, email="approver@test.local")
        session.commit()
        from app.core.context import RequestContext

        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)
        approved = stock_count_service.approve(session, approver_ctx, count.id)
        session.commit()
        assert approved.status is StockCountStatus.APPROVED

        posted = stock_count_service.post(session, approver_ctx, count.id)
        session.commit()
        assert posted.status is StockCountStatus.POSTED
        assert inventory_service.get_stock(session, tenant_a.shop.id, p.id) == Decimal(8)

        rows, _ = inventory_service.list_transactions(session, tenant_a.shop.id, product_id=p.id)
        adjustment = next(r for r in rows if r.qty_delta == Decimal(-2))
        assert adjustment.reason_code is AdjustmentReason.COUNT_CORRECTION
        assert str(count.id) in (adjustment.note or "")

    def test_a_count_can_be_cancelled_before_posting_with_no_stock_effect(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a)
        session.commit()
        inventory_service.record_opening_stock(session, ctx, product_id=p.id, quantity=Decimal(5))
        session.commit()

        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()
        cancelled = stock_count_service.cancel(session, ctx, count.id, reason="Wrong scope")
        session.commit()

        assert cancelled.status is StockCountStatus.CANCELLED
        assert inventory_service.get_stock(session, tenant_a.shop.id, p.id) == Decimal(5)

    def test_cancel_requires_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _product(session, tenant_a)
        session.commit()
        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()

        with pytest.raises(InvalidInputError):
            stock_count_service.cancel(session, ctx, count.id, reason="  ")


class TestAuthorization:
    def test_the_creator_cannot_also_approve_their_own_count(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a)
        session.commit()
        inventory_service.record_opening_stock(session, ctx, product_id=p.id, quantity=Decimal(5))
        session.commit()
        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()
        stock_count_service.start_counting(session, ctx, count.id)
        stock_count_service.enter_counts(
            session, ctx, count.id, [{"product_id": p.id, "counted_quantity": Decimal(5)}]
        )
        stock_count_service.submit_for_review(session, ctx, count.id)
        session.commit()

        with pytest.raises(ForbiddenError):
            stock_count_service.approve(session, ctx, count.id)

    def test_posting_before_approval_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a)
        session.commit()
        inventory_service.record_opening_stock(session, ctx, product_id=p.id, quantity=Decimal(5))
        session.commit()
        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()

        with pytest.raises(ConflictError):
            stock_count_service.post(session, ctx, count.id)


class TestLargeVarianceApproval:
    def test_a_variance_reaching_the_shops_threshold_is_routed_to_an_approval_request_instead_of_approving_directly(
        self, session, tenant_a, set_shop
    ):
        set_shop(tenant_a, stock_count_variance_threshold=Decimal("50"))
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a)
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(100), unit_cost=Decimal("10")
        )
        session.commit()
        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()
        stock_count_service.start_counting(session, ctx, count.id)
        # variance of 90 units * 10 = 900, above the 50 threshold
        stock_count_service.enter_counts(
            session, ctx, count.id, [{"product_id": p.id, "counted_quantity": Decimal(10)}]
        )
        stock_count_service.submit_for_review(session, ctx, count.id)
        session.commit()

        second = factories.make_user(session, tenant_a.shop, email="approver2@test.local")
        session.commit()
        from app.core.context import RequestContext

        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)
        result = stock_count_service.approve(session, approver_ctx, count.id)
        session.commit()

        assert result.status is StockCountStatus.REVIEW  # not yet approved directly
        assert result.requires_approval is True

    def test_with_no_threshold_configured_approval_never_gets_routed_to_the_queue(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = _product(session, tenant_a)
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal(100), unit_cost=Decimal("10")
        )
        session.commit()
        count = stock_count_service.create(session, ctx, title="C", scope=StockCountScope.FULL)
        session.commit()
        stock_count_service.start_counting(session, ctx, count.id)
        stock_count_service.enter_counts(
            session, ctx, count.id, [{"product_id": p.id, "counted_quantity": Decimal(0)}]
        )
        stock_count_service.submit_for_review(session, ctx, count.id)
        session.commit()

        second = factories.make_user(session, tenant_a.shop, email="approver3@test.local")
        session.commit()
        from app.core.context import RequestContext

        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)
        result = stock_count_service.approve(session, approver_ctx, count.id)

        assert result.status is StockCountStatus.APPROVED
