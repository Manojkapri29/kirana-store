"""Task assignment, completion and permissions, plus the generic approval queue's no-self-approval rule."""

import pytest

from app.core.context import RequestContext
from app.models.enums import ApprovalStatus, TaskStatus
from app.services import approval_service, task_service
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError
from tests import factories
from tests.conftest import context_for


class TestTasks:
    def test_a_task_can_be_created_assigned_and_completed(self, session, tenant_a):
        ctx = context_for(tenant_a)
        staff = factories.make_user(session, tenant_a.shop, email="staff@test.local")
        session.commit()

        task = task_service.create(session, ctx, title="Recount rice shelf")
        session.commit()
        assert task.status is TaskStatus.OPEN

        task_service.assign(session, ctx, task.id, staff.id)
        session.commit()
        assert task.assigned_to == staff.id

        completed = task_service.complete(session, ctx, task.id)
        session.commit()
        assert completed.status is TaskStatus.COMPLETED
        assert completed.completed_by == ctx.user_id

    def test_a_completed_task_cannot_be_completed_again(self, session, tenant_a):
        ctx = context_for(tenant_a)
        task = task_service.create(session, ctx, title="T")
        session.commit()
        task_service.complete(session, ctx, task.id)
        session.commit()

        with pytest.raises(ConflictError):
            task_service.complete(session, ctx, task.id)

    def test_assigning_to_someone_outside_the_shop_is_refused(self, session, tenant_a, tenant_b):
        ctx = context_for(tenant_a)
        task = task_service.create(session, ctx, title="T")
        session.commit()

        with pytest.raises(InvalidInputError):
            task_service.assign(session, ctx, task.id, tenant_b.user.id)

    def test_a_blank_title_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)

        with pytest.raises(InvalidInputError):
            task_service.create(session, ctx, title="   ")

    def test_counts_for_only_reflect_that_users_open_and_overdue_tasks(self, session, tenant_a):
        from datetime import timedelta

        ctx = context_for(tenant_a)
        staff = factories.make_user(session, tenant_a.shop, email="staff2@test.local")
        session.commit()
        today = factories.today_in_shop_timezone()
        overdue = task_service.create(
            session, ctx, title="Overdue", assigned_to=staff.id, due_date=today - timedelta(days=2)
        )
        task_service.create(
            session, ctx, title="Not overdue", assigned_to=staff.id, due_date=today + timedelta(days=2)
        )
        session.commit()

        counts = task_service.counts_for(session, tenant_a.shop.id, staff.id, today)

        assert counts.open == 2
        assert counts.overdue == 1
        assert overdue.id  # created fine


class TestApprovals:
    def test_the_requester_cannot_decide_their_own_request(self, session, tenant_a):
        ctx = context_for(tenant_a)
        req = approval_service.create(
            session, ctx, kind="TEST_KIND", entity_type="x", entity_id=1, reason="test"
        )
        session.commit()

        with pytest.raises(ForbiddenError):
            approval_service.decide(session, ctx, req.id, approve=True)

    def test_a_different_user_can_approve_it(self, session, tenant_a):
        ctx = context_for(tenant_a)
        second = factories.make_user(session, tenant_a.shop, email="second@test.local")
        session.commit()
        req = approval_service.create(
            session, ctx, kind="TEST_KIND", entity_type="x", entity_id=1, reason="test"
        )
        session.commit()

        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)
        decided = approval_service.decide(session, approver_ctx, req.id, approve=True)
        session.commit()

        assert decided.request.status is ApprovalStatus.APPROVED

    def test_rejecting_requires_a_note(self, session, tenant_a):
        ctx = context_for(tenant_a)
        second = factories.make_user(session, tenant_a.shop, email="third@test.local")
        session.commit()
        req = approval_service.create(
            session, ctx, kind="TEST_KIND", entity_type="x", entity_id=1, reason="test"
        )
        session.commit()
        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)

        with pytest.raises(InvalidInputError):
            approval_service.decide(session, approver_ctx, req.id, approve=False, note=None)

    def test_a_decided_request_cannot_be_decided_twice(self, session, tenant_a):
        ctx = context_for(tenant_a)
        second = factories.make_user(session, tenant_a.shop, email="fourth@test.local")
        session.commit()
        req = approval_service.create(
            session, ctx, kind="TEST_KIND", entity_type="x", entity_id=1, reason="test"
        )
        session.commit()
        approver_ctx = RequestContext(shop_id=tenant_a.shop.id, user_id=second.id, role=ctx.role)
        approval_service.decide(session, approver_ctx, req.id, approve=True)
        session.commit()

        with pytest.raises(ConflictError):
            approval_service.decide(session, approver_ctx, req.id, approve=True)
