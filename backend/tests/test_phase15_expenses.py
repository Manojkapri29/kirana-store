"""Expenses: the DRAFT -> SUBMITTED -> APPROVED -> POSTED lifecycle, configurable categories, the approval
threshold, and the rule that only a POSTED expense reaches the books (a void reverses it, nothing is deleted)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.context import RequestContext
from app.models import AuditLog, FinanceEntry
from app.models.enums import ExpenseStatus, FinanceEventType, FinancePaymentMethod, FlowDirection
from app.services import (
    expense_service,
    finance_period_service,
    finance_settings_service,
)
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


def _category(session, ctx, name="Rent"):
    c = expense_service.create_category(session, ctx, name=name)
    session.commit()
    return c


def _draft(session, ctx, category, amount="500.00", **over):
    data = {
        "expense_date": TODAY, "category_id": category.id, "amount": Decimal(amount),
        "payment_method": FinancePaymentMethod.CASH, "payee": "Landlord", "description": "Shop rent",
    }  # fmt: skip
    data.update(over)
    e = expense_service.create_expense(session, ctx, data)
    session.commit()
    return e


def _second(session, tenant, ctx, email="second@test.local"):
    user = factories.make_user(session, tenant.shop, email=email)
    session.commit()
    return RequestContext(shop_id=tenant.shop.id, user_id=user.id, role=ctx.role)


def _posted(session, ctx, category, amount="500.00"):
    e = _draft(session, ctx, category, amount)
    expense_service.submit_expense(session, ctx, e.id)
    expense_service.post_expense(session, ctx, e.id)
    session.commit()
    return e


def _entries(session, shop_id):
    return session.scalar(
        select(func.count()).select_from(FinanceEntry).where(FinanceEntry.shop_id == shop_id)
    )


class TestCategories:
    def test_categories_are_configurable_not_a_fixed_list(self, session, tenant_a):
        ctx = context_for(tenant_a)
        assert expense_service.list_categories(session, tenant_a.shop.id) == []
        for name in ("Rent", "Electricity", "Tea for staff", "Delivery van repair"):
            expense_service.create_category(session, ctx, name=name)
        session.commit()
        assert len(expense_service.list_categories(session, tenant_a.shop.id)) == 4

    def test_a_duplicate_name_is_refused_ignoring_case(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _category(session, ctx, "Rent")
        with pytest.raises(ConflictError):
            expense_service.create_category(session, ctx, name="rent")

    def test_a_deactivated_category_cannot_be_used_but_old_expenses_keep_it(self, session, tenant_a):
        ctx = context_for(tenant_a)
        cat = _category(session, ctx)
        old = _draft(session, ctx, cat)
        expense_service.update_category(session, ctx, cat.id, {"is_active": False})
        session.commit()
        with pytest.raises(InvalidInputError):
            _draft(session, ctx, cat)
        assert expense_service.get_expense(session, tenant_a.shop.id, old.id).category_id == cat.id


class TestDraftsDoNotCount:
    def test_a_draft_creates_no_ledger_entry_and_no_reported_expense(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _draft(session, ctx, _category(session, ctx))

        assert _entries(session, tenant_a.shop.id) == 0
        assert expense_service.posted_expense_total(session, tenant_a.shop.id, TODAY, TODAY) == Decimal(
            "0.00"
        )

    def test_submitted_approved_and_rejected_expenses_do_not_count_either(self, session, tenant_a):
        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": Decimal("100")})
        cat = _category(session, ctx)
        submitted = _draft(session, ctx, cat)
        expense_service.submit_expense(session, ctx, submitted.id)
        approver = _second(session, tenant_a, ctx)
        rejected = _draft(session, ctx, cat)
        expense_service.submit_expense(session, ctx, rejected.id)
        expense_service.decide(session, approver, rejected.id, approve=False, note="No receipt")
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": None})
        approved = _draft(session, ctx, cat)
        expense_service.submit_expense(session, ctx, approved.id)
        session.commit()

        assert approved.status is ExpenseStatus.APPROVED and rejected.status is ExpenseStatus.REJECTED
        assert expense_service.posted_expense_total(session, tenant_a.shop.id, TODAY, TODAY) == Decimal(
            "0.00"
        )


class TestApprovalThreshold:
    def test_no_threshold_configured_means_submitting_approves(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _draft(session, ctx, _category(session, ctx), "99999.00")

        submitted = expense_service.submit_expense(session, ctx, e.id)

        assert submitted.status is ExpenseStatus.APPROVED and submitted.requires_approval is False

    def test_below_the_threshold_is_approved_at_or_above_it_waits(self, session, tenant_a):
        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(
            session, ctx, {"expense_approval_threshold": Decimal("1000")}
        )
        cat = _category(session, ctx)
        small = expense_service.submit_expense(session, ctx, _draft(session, ctx, cat, "999.99").id)
        big = expense_service.submit_expense(session, ctx, _draft(session, ctx, cat, "1000.00").id)

        assert small.status is ExpenseStatus.APPROVED
        assert big.status is ExpenseStatus.SUBMITTED and big.requires_approval is True

    def test_the_submitter_cannot_approve_their_own_expense(self, session, tenant_a):
        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": Decimal("100")})
        e = expense_service.submit_expense(session, ctx, _draft(session, ctx, _category(session, ctx)).id)
        session.commit()

        with pytest.raises(ForbiddenError):
            expense_service.decide(session, ctx, e.id, approve=True)

    def test_another_person_approves_and_it_is_audited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": Decimal("100")})
        e = expense_service.submit_expense(session, ctx, _draft(session, ctx, _category(session, ctx)).id)
        approver = _second(session, tenant_a, ctx)

        approved = expense_service.decide(session, approver, e.id, approve=True)
        session.commit()

        assert approved.status is ExpenseStatus.APPROVED and approved.approved_by == approver.user_id
        actions = set(session.scalars(select(AuditLog.action)))
        assert {"expense_submitted", "approval_requested", "approval_decided", "expense_approved"} <= actions

    def test_a_rejection_needs_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": Decimal("100")})
        e = expense_service.submit_expense(session, ctx, _draft(session, ctx, _category(session, ctx)).id)
        approver = _second(session, tenant_a, ctx)
        with pytest.raises(InvalidInputError):
            expense_service.decide(session, approver, e.id, approve=False, note=" ")


class TestPostingAndVoiding:
    def test_only_an_approved_expense_can_be_posted(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _draft(session, ctx, _category(session, ctx))
        with pytest.raises(ConflictError):
            expense_service.post_expense(session, ctx, e.id)

    def test_posting_writes_one_traceable_ledger_entry(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx), "250.50")

        entry = session.scalar(select(FinanceEntry))
        assert (entry.event_type, entry.direction, entry.amount) == (
            FinanceEventType.EXPENSE, FlowDirection.OUT, Decimal("250.50"),
        )  # fmt: skip
        assert (entry.reference_type, entry.reference_id) == ("EXPENSE", e.id)
        assert expense_service.posted_expense_total(session, tenant_a.shop.id, TODAY, TODAY) == Decimal(
            "250.50"
        )

    def test_posting_twice_never_posts_twice(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx))

        again, created = expense_service.post_expense(session, ctx, e.id)
        session.commit()

        assert created is False and again.status is ExpenseStatus.POSTED
        assert _entries(session, tenant_a.shop.id) == 1

    def test_a_void_reverses_the_impact_and_keeps_the_history(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx), "400.00")

        voided = expense_service.void_expense(session, ctx, e.id, "Entered twice", today=TODAY)
        session.commit()

        assert voided.status is ExpenseStatus.VOIDED and voided.void_reason == "Entered twice"
        assert expense_service.posted_expense_total(session, tenant_a.shop.id, TODAY, TODAY) == Decimal(
            "0.00"
        )
        assert _entries(session, tenant_a.shop.id) == 2  # the original and its reversal: nothing deleted

    def test_voiding_twice_adds_nothing(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx))
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        session.commit()
        assert _entries(session, tenant_a.shop.id) == 2

    def test_a_void_needs_a_reason_and_a_voided_expense_cannot_be_posted(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx))
        with pytest.raises(InvalidInputError):
            expense_service.void_expense(session, ctx, e.id, "  ", today=TODAY)
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        with pytest.raises(ConflictError):
            expense_service.post_expense(session, ctx, e.id)

    def test_only_posted_expenses_can_be_voided(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _draft(session, ctx, _category(session, ctx))
        with pytest.raises(ConflictError):
            expense_service.void_expense(session, ctx, e.id, "x", today=TODAY)

    def test_the_finance_ledger_table_is_insert_only(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _posted(session, ctx, _category(session, ctx))
        entry = session.scalar(select(FinanceEntry))
        entry.amount = Decimal("1.00")
        with pytest.raises(Exception, match="insert-only"):
            session.flush()
        session.rollback()


class TestEditing:
    def test_only_a_draft_or_rejected_expense_can_be_edited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _posted(session, ctx, _category(session, ctx))
        with pytest.raises(ConflictError):
            expense_service.update_expense(session, ctx, e.id, {"amount": Decimal("1.00")})

    def test_editing_a_draft_changes_it(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e = _draft(session, ctx, _category(session, ctx))
        changed = expense_service.update_expense(
            session, ctx, e.id, {"amount": Decimal("12.34"), "payee": "Ravi"}
        )
        assert changed.amount == Decimal("12.34") and changed.payee == "Ravi"


class TestMoneyIsExact:
    @pytest.mark.parametrize("bad", [12.5, "12.50", Decimal("0"), Decimal("-1"), Decimal("1.234")])
    def test_amounts_that_are_not_exact_positive_paise_are_refused(self, session, tenant_a, bad):
        ctx = context_for(tenant_a)
        cat = _category(session, ctx)
        with pytest.raises(InvalidInputError):
            expense_service.create_expense(
                session,
                ctx,
                {"expense_date": TODAY, "category_id": cat.id, "amount": bad, "payment_method": "CASH"},
            )

    def test_the_stored_amount_is_integer_paise(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _posted(session, ctx, _category(session, ctx), "250.55")
        stored = session.connection().exec_driver_sql("select amount from finance_entries").scalar()
        assert stored == 25055 and isinstance(stored, int)


class TestPeriods:
    def test_posting_into_a_closed_period_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        cat = _category(session, ctx)
        e = _draft(session, ctx, cat, expense_date=TODAY - timedelta(days=40))
        expense_service.submit_expense(session, ctx, e.id)
        period = finance_period_service.create_period(
            session, ctx, period_start=TODAY - timedelta(days=60), period_end=TODAY - timedelta(days=30)
        )
        finance_period_service.close(session, ctx, period.id)
        session.commit()

        with pytest.raises(ConflictError) as info:
            expense_service.post_expense(session, ctx, e.id)
        assert info.value.code == "period_closed"

    def test_voiding_an_expense_from_a_closed_period_reverses_it_in_todays_open_period(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        cat = _category(session, ctx)
        past = TODAY - timedelta(days=40)
        e = _draft(session, ctx, cat, "300.00", expense_date=past)
        expense_service.submit_expense(session, ctx, e.id)
        expense_service.post_expense(session, ctx, e.id)
        period = finance_period_service.create_period(
            session, ctx, period_start=past - timedelta(days=20), period_end=past + timedelta(days=5)
        )
        finance_period_service.close(session, ctx, period.id)
        session.commit()

        expense_service.void_expense(session, ctx, e.id, "Duplicate", today=TODAY)
        session.commit()

        # The closed period is untouched; the correction lives in the open period.
        assert expense_service.posted_expense_total(
            session, tenant_a.shop.id, past - timedelta(days=20), past + timedelta(days=5)
        ) == Decimal("300.00")
        assert expense_service.posted_expense_total(session, tenant_a.shop.id, TODAY, TODAY) == Decimal(
            "-300.00"
        )


class TestByCategory:
    def test_net_expense_per_category_after_a_void(self, session, tenant_a):
        ctx = context_for(tenant_a)
        rent, tea = _category(session, ctx, "Rent"), _category(session, ctx, "Tea")
        _posted(session, ctx, rent, "1000.00")
        voided = _posted(session, ctx, tea, "50.00")
        _posted(session, ctx, tea, "30.00")
        expense_service.void_expense(session, ctx, voided.id, "Mistake", today=TODAY)
        session.commit()

        rows = expense_service.expenses_by_category(session, tenant_a.shop.id, TODAY, TODAY)

        assert [(name, total) for _, name, total in rows] == [
            ("Rent", Decimal("1000.00")),
            ("Tea", Decimal("30.00")),
        ]


class TestApi:
    def test_the_full_lifecycle_over_http(self, session, tenant_a, client_a):
        cat = client_a.post("/api/v1/finance/expense-categories", json={"name": "Rent"}).json()
        created = client_a.post(
            "/api/v1/finance/expenses",
            json={
                "expense_date": TODAY.isoformat(),
                "category_id": cat["id"],
                "amount": "750.00",
                "payment_method": "UPI",
            },
        )
        assert created.status_code == 201, created.text
        expense = created.json()
        assert expense["status"] == "DRAFT" and expense["expense_no"].startswith("EXP/")
        assert (
            client_a.post(f"/api/v1/finance/expenses/{expense['id']}/submit").json()["status"] == "APPROVED"
        )
        posted = client_a.post(f"/api/v1/finance/expenses/{expense['id']}/post")
        assert posted.json()["status"] == "POSTED"
        assert client_a.post(f"/api/v1/finance/expenses/{expense['id']}/post").json()["status"] == "POSTED"
        ledger = client_a.get("/api/v1/finance/ledger", params={"event_type": "EXPENSE"}).json()
        assert ledger["total"] == 1 and ledger["total_out"] == "750.00"
        voided = client_a.post(
            f"/api/v1/finance/expenses/{expense['id']}/void", json={"reason": "Wrong shop"}
        )
        assert voided.json()["status"] == "VOIDED"

    def test_an_idempotency_key_makes_a_repeated_create_return_the_same_expense(
        self, session, tenant_a, client_a
    ):
        cat = client_a.post("/api/v1/finance/expense-categories", json={"name": "Rent"}).json()
        body = {
            "expense_date": TODAY.isoformat(),
            "category_id": cat["id"],
            "amount": "10.00",
            "payment_method": "CASH",
        }
        headers = {"Idempotency-Key": "expense-create-0001"}
        first = client_a.post("/api/v1/finance/expenses", json=body, headers=headers).json()
        second = client_a.post("/api/v1/finance/expenses", json=body, headers=headers).json()
        assert first["id"] == second["id"]
        assert client_a.get("/api/v1/finance/expenses").json()["total"] == 1

    def test_another_shops_expense_is_not_found(self, session, tenant_a, tenant_b, client_b):
        ctx = context_for(tenant_a)
        e = _draft(session, ctx, _category(session, ctx))
        assert client_b.get(f"/api/v1/finance/expenses/{e.id}").status_code == 404
        assert client_b.post(f"/api/v1/finance/expenses/{e.id}/submit").status_code == 404
        with pytest.raises(NotFoundError):
            expense_service.get_expense(session, tenant_b.shop.id, e.id)
