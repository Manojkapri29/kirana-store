"""The financial ledger: one traceable view over every money event, idempotent posting, reversals instead of
edits, and period controls (OPEN / LOCKED / CLOSED)."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.models import AuditLog, FinanceEntry
from app.models.enums import (
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PaymentMethod,
    PeriodStatus,
    RefundMode,
    SaleStatus,
)
from app.services import (
    finance_ledger_service,
    finance_period_service,
    finance_settings_service,
    khata_service,
)
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import (
    D,
    make_purchase,
    make_purchase_return,
    make_quick_sale,
    make_sale,
    make_sales_return,
)

TODAY = today_in_shop_timezone()


def _rows(session, tenant, **kw):
    return finance_ledger_service.list_ledger(
        session, tenant.shop.id, TODAY - timedelta(days=30), TODAY, **kw
    )


def _entry(session, ctx, event=FinanceEventType.OTHER_INCOME, amount="10.00", day=TODAY, **kw):
    direction = kw.pop("direction", FlowDirection.IN)
    entry, created = finance_ledger_service.record_entry(
        session,
        ctx,
        event_type=event,
        direction=direction,
        amount=D(amount),
        payment_method=kw.pop("payment_method", FinancePaymentMethod.CASH),
        entry_date=day,
        **kw,
    )
    session.commit()
    return entry, created


class TestOneViewOverEveryMoneyEvent:
    def test_every_source_appears_and_points_back_to_its_document(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        sale = make_sale(session, tenant_a, "100.00", day=TODAY, customer_id=customer.id)
        quick = make_quick_sale(session, tenant_a, "50.00", day=TODAY)
        purchase = make_purchase(session, tenant_a, supplier, "400.00", day=TODAY)
        sret = make_sales_return(session, tenant_a, sale, "20.00", day=TODAY, mode=RefundMode.CASH)
        pret = make_purchase_return(session, tenant_a, purchase, "40.00", day=TODAY)
        session.commit()
        khata_service.record_payment(
            session, ctx, customer.id, D("30.00"), entry_date=TODAY, payment_method=PaymentMethod.CASH
        )
        entry, _ = _entry(session, ctx)

        rows = {(r.source_type, r.source_id): r for r in _rows(session, tenant_a)}

        assert {t for t, _ in rows} == {
            "SALE",
            "QUICK_SALE",
            "PURCHASE",
            "SALES_RETURN",
            "PURCHASE_RETURN",
            "KHATA_PAYMENT",
            "FINANCE_ENTRY",
        }
        assert rows[("SALE", sale.id)].reference == sale.invoice_no
        assert rows[("QUICK_SALE", quick.id)].reference == quick.quick_no
        assert rows[("PURCHASE", purchase.id)].supplier_id == supplier.id
        assert rows[("SALES_RETURN", sret.id)].event_type is FinanceEventType.SALE_RETURN
        assert rows[("PURCHASE_RETURN", pret.id)].event_type is FinanceEventType.PURCHASE_RETURN
        assert rows[("FINANCE_ENTRY", entry.id)].created_by == ctx.user_id
        assert all(r.entry_date and r.payment_method and r.source_module for r in rows.values())

    def test_a_sale_settles_only_what_was_paid_the_rest_is_khata(self, session, tenant_a):
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        make_sale(session, tenant_a, "500.00", day=TODAY, paid="200.00", customer_id=customer.id)
        session.commit()
        row = _rows(session, tenant_a)[0]
        assert (row.amount, row.settled_amount) == (D("500.00"), D("200.00"))

    def test_voided_and_draft_documents_are_left_out(self, session, tenant_a):
        sale = make_sale(session, tenant_a, "100.00", day=TODAY)
        sale.status = SaleStatus.VOID
        sale.void_reason = "x"
        session.commit()
        assert _rows(session, tenant_a) == []

    def test_a_reversed_khata_payment_disappears_from_the_ledger(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        result = khata_service.record_payment(
            session, ctx, c.id, D("30.00"), entry_date=TODAY, payment_method=PaymentMethod.CASH
        )
        khata_service.reverse_entry(session, ctx, result.entry.id, reason="Wrong customer", customer_id=c.id)
        session.commit()
        assert [r for r in _rows(session, tenant_a) if r.source_type == "KHATA_PAYMENT"] == []

    def test_filters(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        make_sale(session, tenant_a, "100.00", day=TODAY, customer_id=customer.id)
        make_quick_sale(session, tenant_a, "50.00", day=TODAY, method=PaymentMethod.UPI)
        make_purchase(session, tenant_a, supplier, "400.00", day=TODAY - timedelta(days=3))
        session.commit()
        _entry(
            session,
            ctx,
            FinanceEventType.SUPPLIER_PAYMENT,
            "40.00",
            direction=FlowDirection.OUT,
            supplier_id=supplier.id,
        )

        assert len(_rows(session, tenant_a, event_types=[FinanceEventType.SALE])) == 2
        assert len(_rows(session, tenant_a, payment_method="UPI")) == 1
        assert len(_rows(session, tenant_a, customer_id=customer.id)) == 1
        assert len(_rows(session, tenant_a, supplier_id=supplier.id)) == 2
        assert len(_rows(session, tenant_a, source_type="PURCHASE")) == 1
        assert len(_rows(session, tenant_a, reference="INV/T")) == 1
        rows = finance_ledger_service.list_ledger(session, tenant_a.shop.id, TODAY, TODAY)
        assert all(r.entry_date == TODAY for r in rows)

    def test_the_view_creates_no_rows_of_its_own(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()
        _rows(session, tenant_a)
        assert session.scalar(select(func.count()).select_from(FinanceEntry)) == 0


class TestIdempotentPosting:
    def test_the_same_source_is_recorded_once(self, session, tenant_a):
        ctx = context_for(tenant_a)
        first, created1 = _entry(session, ctx, reference_type="DOC", reference_id=7)
        second, created2 = _entry(session, ctx, reference_type="DOC", reference_id=7)
        assert (created1, created2) == (True, False) and first.id == second.id
        assert session.scalar(select(func.count()).select_from(FinanceEntry)) == 1

    def test_a_reference_needs_both_parts(self, session, tenant_a):
        with pytest.raises(InvalidInputError):
            _entry(session, context_for(tenant_a), reference_type="DOC")

    def test_amounts_must_be_exact_positive_paise(self, session, tenant_a):
        ctx = context_for(tenant_a)
        for bad in (0, -5, 1.5, "2.00", D("1.005")):
            with pytest.raises(InvalidInputError):
                finance_ledger_service.record_entry(
                    session,
                    ctx,
                    event_type=FinanceEventType.OTHER_INCOME,
                    direction=FlowDirection.IN,
                    amount=bad,
                    payment_method=FinancePaymentMethod.CASH,
                    entry_date=TODAY,
                )


class TestReversalsNotEdits:
    def test_a_reversal_mirrors_the_entry_and_both_stay(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e, _ = _entry(session, ctx, FinanceEventType.OWNER_CAPITAL, "1000.00")
        mirror, created = finance_ledger_service.reverse_entry(
            session, ctx, e.id, "Wrong amount", today=TODAY
        )
        session.commit()
        assert created and mirror.direction is FlowDirection.OUT and mirror.reverses_entry_id == e.id
        statuses = {r.status for r in _rows(session, tenant_a)}
        assert statuses == {"REVERSED", "REVERSAL"}
        net = sum(finance_ledger_service.signed_settled(r) for r in _rows(session, tenant_a))
        assert net == D("0.00")

    def test_reversing_twice_changes_nothing(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e, _ = _entry(session, ctx)
        finance_ledger_service.reverse_entry(session, ctx, e.id, "x", today=TODAY)
        again, created = finance_ledger_service.reverse_entry(session, ctx, e.id, "x", today=TODAY)
        session.commit()
        assert created is False
        assert session.scalar(select(func.count()).select_from(FinanceEntry)) == 2

    def test_a_reversal_cannot_be_reversed_and_needs_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e, _ = _entry(session, ctx)
        with pytest.raises(InvalidInputError):
            finance_ledger_service.reverse_entry(session, ctx, e.id, " ", today=TODAY)
        mirror, _ = finance_ledger_service.reverse_entry(session, ctx, e.id, "x", today=TODAY)
        with pytest.raises(ConflictError):
            finance_ledger_service.reverse_entry(session, ctx, mirror.id, "undo", today=TODAY)

    def test_posted_history_cannot_be_edited_or_deleted_at_the_database(self, session, tenant_a):
        _entry(session, context_for(tenant_a))
        for sql in ("UPDATE finance_entries SET note = 'x'", "DELETE FROM finance_entries"):
            with pytest.raises(Exception, match="insert-only"):
                session.execute(text(sql))
            session.rollback()

    def test_every_recording_and_reversal_is_audited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        e, _ = _entry(session, ctx)
        finance_ledger_service.reverse_entry(session, ctx, e.id, "x", today=TODAY)
        session.commit()
        assert {"finance_entry_recorded", "finance_entry_reversed"} <= set(
            session.scalars(select(AuditLog.action))
        )


class TestPeriodControls:
    def _period(self, session, ctx, days_ago_start=40, days_ago_end=30):
        p = finance_period_service.create_period(
            session,
            ctx,
            period_start=TODAY - timedelta(days=days_ago_start),
            period_end=TODAY - timedelta(days=days_ago_end),
        )
        session.commit()
        return p

    def test_a_date_with_no_period_is_open(self, session, tenant_a):
        assert finance_period_service.status_on(session, tenant_a.shop.id, TODAY) is PeriodStatus.OPEN

    def test_overlapping_periods_are_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        self._period(session, ctx)
        with pytest.raises(ConflictError):
            finance_period_service.create_period(
                session, ctx, period_start=TODAY - timedelta(days=35), period_end=TODAY - timedelta(days=20)
            )

    def test_open_locked_closed_transitions(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = self._period(session, ctx)
        assert finance_period_service.lock(session, ctx, p.id).status is PeriodStatus.LOCKED
        with pytest.raises(ConflictError):
            finance_period_service.lock(session, ctx, p.id)
        assert finance_period_service.unlock(session, ctx, p.id).status is PeriodStatus.OPEN
        finance_period_service.close(session, ctx, p.id)
        with pytest.raises(ConflictError):
            finance_period_service.unlock(session, ctx, p.id)  # closed needs reopen
        assert (
            finance_period_service.reopen(session, ctx, p.id, "Late invoice found").status
            is PeriodStatus.OPEN
        )

    def test_a_locked_period_refuses_ordinary_entries_but_accepts_a_controlled_adjustment(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        p = self._period(session, ctx)
        finance_period_service.lock(session, ctx, p.id)
        session.commit()
        day = TODAY - timedelta(days=35)
        with pytest.raises(ConflictError) as info:
            _entry(session, ctx, day=day)
        assert info.value.code == "period_locked"
        session.rollback()
        outcome = finance_ledger_service.record_adjustment(
            session,
            ctx,
            direction=FlowDirection.IN,
            amount=D("5.00"),
            payment_method=FinancePaymentMethod.OTHER,
            entry_date=day,
            note="Late correction",
        )
        assert outcome.entry is not None

    def test_a_closed_period_refuses_even_an_adjustment(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = self._period(session, ctx)
        finance_period_service.close(session, ctx, p.id)
        session.commit()
        day = TODAY - timedelta(days=35)
        with pytest.raises(ConflictError) as info:
            finance_ledger_service.record_adjustment(
                session,
                ctx,
                direction=FlowDirection.IN,
                amount=D("5.00"),
                payment_method=FinancePaymentMethod.OTHER,
                entry_date=day,
                note="Try",
            )
        assert info.value.code == "period_closed"

    def test_a_correction_for_a_closed_period_is_dated_in_an_open_one(self, session, tenant_a):
        ctx = context_for(tenant_a)
        day = TODAY - timedelta(days=35)
        e, _ = _entry(session, ctx, day=day)
        p = self._period(session, ctx)
        finance_period_service.close(session, ctx, p.id)
        session.commit()
        mirror, _ = finance_ledger_service.reverse_entry(session, ctx, e.id, "Wrong", today=TODAY)
        assert mirror.entry_date == TODAY and e.entry_date == day  # the closed period is untouched

    def test_reopening_can_require_a_second_persons_approval(self, session, tenant_a):
        from app.core.context import RequestContext
        from app.services import approval_service

        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"period_reopen_requires_approval": True})
        p = self._period(session, ctx)
        finance_period_service.close(session, ctx, p.id)
        session.commit()

        held = finance_period_service.reopen(session, ctx, p.id, "Need to fix")
        session.commit()
        assert held.status is PeriodStatus.CLOSED and finance_period_service.has_pending_reopen(session, held)
        finance_period_service.reopen(
            session, ctx, p.id, "Need to fix"
        )  # asking again adds no second request
        other = factories.make_user(session, tenant_a.shop, email="boss@test.local")
        session.commit()
        pending = approval_service.list_pending(session, tenant_a.shop.id)
        assert len(pending) == 1
        approval_service.decide(
            session,
            RequestContext(shop_id=tenant_a.shop.id, user_id=other.id, role=ctx.role),
            pending[0].id,
            approve=True,
        )
        session.commit()

        assert finance_period_service.reopen(session, ctx, p.id, "Need to fix").status is PeriodStatus.OPEN
        session.commit()
        finance_period_service.close(session, ctx, p.id)
        held_again = finance_period_service.reopen(
            session, ctx, p.id, "Again"
        )  # the old approval does not carry over
        assert held_again.status is PeriodStatus.CLOSED

    def test_every_period_action_is_audited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = self._period(session, ctx)
        finance_period_service.lock(session, ctx, p.id)
        finance_period_service.unlock(session, ctx, p.id)
        finance_period_service.close(session, ctx, p.id)
        finance_period_service.reopen(session, ctx, p.id, "why")
        session.commit()
        assert {
            "period_created",
            "period_locked",
            "period_unlocked",
            "period_closed",
            "period_reopened",
        } <= set(session.scalars(select(AuditLog.action)))

    def test_a_reopen_needs_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        p = self._period(session, ctx)
        finance_period_service.close(session, ctx, p.id)
        with pytest.raises(InvalidInputError):
            finance_period_service.reopen(session, ctx, p.id, " ")


class TestApi:
    def test_manual_entries_are_limited_to_the_four_recordable_types(self, session, tenant_a, client_a):
        body = {
            "event_type": "SALE",
            "amount": "5.00",
            "payment_method": "CASH",
            "entry_date": TODAY.isoformat(),
        }
        r = client_a.post("/api/v1/finance/entries", json=body)
        assert r.status_code in (400, 422)

    def test_a_supplier_payment_needs_a_supplier_and_shows_in_the_ledger(self, session, tenant_a, client_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()
        body = {
            "event_type": "SUPPLIER_PAYMENT",
            "amount": "40.00",
            "payment_method": "BANK_TRANSFER",
            "entry_date": TODAY.isoformat(),
        }
        assert client_a.post("/api/v1/finance/entries", json=body).status_code in (400, 422)
        ok = client_a.post(
            "/api/v1/finance/entries",
            json={**body, "supplier_id": supplier.id},
            headers={"Idempotency-Key": "pay-supplier-001"},
        )
        assert ok.status_code == 201, ok.text
        again = client_a.post(
            "/api/v1/finance/entries",
            json={**body, "supplier_id": supplier.id},
            headers={"Idempotency-Key": "pay-supplier-001"},
        )
        assert again.json()["id"] == ok.json()["id"]
        ledger = client_a.get("/api/v1/finance/ledger", params={"supplier_id": supplier.id}).json()
        assert ledger["total"] == 1 and ledger["total_out"] == "40.00"

    def test_a_large_adjustment_answers_202_and_records_nothing(self, session, tenant_a, client_a):
        finance_settings_service.update_settings(
            session, context_for(tenant_a), {"adjustment_approval_threshold": D("10.00")}
        )
        session.commit()
        body = {
            "direction": "OUT",
            "amount": "50.00",
            "payment_method": "BANK_TRANSFER",
            "entry_date": TODAY.isoformat(),
            "note": "Bank charges",
        }
        r = client_a.post("/api/v1/finance/adjustments", json=body)
        assert r.status_code == 202 and r.json()["status"] == "APPROVAL_REQUIRED"
        assert client_a.get("/api/v1/finance/ledger").json()["total"] == 0

    def test_a_blocked_period_attempt_is_recorded_and_survives_the_rollback(
        self, session, tenant_a, client_a
    ):
        from app.models import SystemEvent

        ctx = context_for(tenant_a)
        p = finance_period_service.create_period(
            session, ctx, period_start=TODAY - timedelta(days=9), period_end=TODAY - timedelta(days=5)
        )
        finance_period_service.close(session, ctx, p.id)
        session.commit()
        body = {
            "event_type": "OTHER_INCOME",
            "amount": "5.00",
            "payment_method": "CASH",
            "entry_date": (TODAY - timedelta(days=7)).isoformat(),
        }
        r = client_a.post("/api/v1/finance/entries", json=body)
        assert r.status_code == 409
        events = session.scalars(
            select(SystemEvent).where(SystemEvent.code == finance_period_service.BLOCKED_EVENT_CODE)
        ).all()
        assert len(events) == 1 and "CLOSED" in events[0].message
        assert (
            client_a.get(
                "/api/v1/finance/ledger", params={"date_from": (TODAY - timedelta(days=9)).isoformat()}
            ).json()["total"]
            == 0
        )

    def test_another_shop_sees_none_of_it_and_cannot_reverse_it(self, session, tenant_a, tenant_b, client_b):
        e, _ = _entry(session, context_for(tenant_a))
        assert client_b.get("/api/v1/finance/ledger").json()["total"] == 0
        assert (
            client_b.post(f"/api/v1/finance/entries/{e.id}/reverse", json={"reason": "x"}).status_code == 404
        )
        with pytest.raises(NotFoundError):
            finance_ledger_service.get_entry(session, tenant_b.shop.id, e.id)


class TestSourceDocumentsRespectClosedPeriods:
    """Closing a period stops direct changes to the sales and purchases dated in it, not just finance entries."""

    def _close(self, session, ctx, start_days, end_days):
        p = finance_period_service.create_period(
            session,
            ctx,
            period_start=TODAY - timedelta(days=start_days),
            period_end=TODAY - timedelta(days=end_days),
        )
        finance_period_service.close(session, ctx, p.id)
        session.commit()

    def test_a_posted_quick_sale_in_a_closed_period_cannot_be_voided(self, session, tenant_a):
        from app.services import quick_sale_service

        ctx = context_for(tenant_a)
        sale = make_quick_sale(session, tenant_a, "100.00", day=TODAY - timedelta(days=20))
        session.commit()
        self._close(session, ctx, 30, 10)
        with pytest.raises(ConflictError) as info:
            quick_sale_service.void_quick_sale(session, ctx, sale.id, "Wrong")
        assert info.value.code == "period_closed"

    def test_a_draft_quick_sale_dated_in_a_closed_period_cannot_be_posted(self, session, tenant_a):
        from app.services import quick_sale_service

        ctx = context_for(tenant_a)
        draft = quick_sale_service.create_quick_sale(
            session, ctx, {"gross_amount": D("50.00"), "sale_date": TODAY - timedelta(days=20)}
        )
        session.commit()
        self._close(session, ctx, 30, 10)
        with pytest.raises(ConflictError):
            quick_sale_service.post_quick_sale(session, ctx, draft.sale.id, payment_method=PaymentMethod.CASH)

    def test_a_posted_purchase_in_a_closed_period_cannot_be_voided(self, session, tenant_a):
        from app.services import purchase_service

        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        purchase = make_purchase(session, tenant_a, supplier, "100.00", day=TODAY - timedelta(days=20))
        session.commit()
        self._close(session, ctx, 30, 10)
        with pytest.raises(ConflictError):
            purchase_service.void_purchase(session, ctx, purchase.id, "Wrong")

    def test_open_dates_are_unaffected_by_a_closed_period_elsewhere(self, session, tenant_a):
        from app.services import quick_sale_service

        ctx = context_for(tenant_a)
        self._close(session, ctx, 30, 10)
        draft = quick_sale_service.create_quick_sale(
            session, ctx, {"gross_amount": D("50.00"), "sale_date": TODAY}
        )
        session.commit()
        posted = quick_sale_service.post_quick_sale(
            session, ctx, draft.sale.id, payment_method=PaymentMethod.CASH
        )
        assert posted.sale.status is SaleStatus.POSTED


class TestAudit:
    """Findings from the Phase 15 audit, kept as regression tests."""

    def test_another_shops_supplier_or_customer_is_not_found_not_a_database_error(
        self, session, tenant_a, tenant_b, client_a
    ):
        supplier_b = factories.make_supplier(session, tenant_b.shop)
        customer_b = factories.make_customer(session, tenant_b.shop)
        session.commit()
        body = {
            "event_type": "SUPPLIER_PAYMENT",
            "amount": "5.00",
            "payment_method": "CASH",
            "entry_date": TODAY.isoformat(),
        }
        assert (
            client_a.post("/api/v1/finance/entries", json={**body, "supplier_id": supplier_b.id}).status_code
            == 404
        )
        with pytest.raises(NotFoundError):
            finance_ledger_service.record_entry(
                session,
                context_for(tenant_a),
                event_type=FinanceEventType.OTHER_INCOME,
                direction=FlowDirection.IN,
                amount=D("5.00"),
                payment_method=FinancePaymentMethod.CASH,
                entry_date=TODAY,
                customer_id=customer_b.id,
            )
        assert client_a.get("/api/v1/finance/ledger").json()["total"] == 0

    def test_a_tax_rate_for_another_shops_category_is_not_found(self, session, tenant_a, tenant_b, client_a):
        r = client_a.post(
            "/api/v1/finance/tax/rates",
            json={"name": "X", "rate_percent": "5", "category_id": tenant_b.category.id},
        )
        assert r.status_code == 404

    def test_an_adjustment_with_an_idempotency_key_is_recorded_once(self, session, tenant_a, client_a):
        body = {
            "direction": "IN",
            "amount": "7.00",
            "payment_method": "CASH",
            "entry_date": TODAY.isoformat(),
            "note": "Found",
        }
        headers = {"Idempotency-Key": "adjust-once-0001"}
        first = client_a.post("/api/v1/finance/adjustments", json=body, headers=headers)
        second = client_a.post("/api/v1/finance/adjustments", json=body, headers=headers)
        assert first.status_code == 201 and second.json()["id"] == first.json()["id"]
        assert client_a.get("/api/v1/finance/ledger").json()["total"] == 1
