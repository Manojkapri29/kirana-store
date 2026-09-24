"""Reconciliation foundation: reviewing the shop's own electronic payment records. No bank data is ever invented."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.models.enums import FinanceEventType, FinancePaymentMethod, FlowDirection, PaymentMethod, ReconStatus
from app.services import finance_ledger_service, reconciliation_service
from app.services.errors import InvalidInputError, NotFoundError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()


def _summary(session, tenant, **kw):
    return reconciliation_service.summary(session, tenant.shop.id, TODAY - timedelta(days=5), TODAY, **kw)


class TestSummary:
    def test_only_electronic_payments_appear_and_default_to_unmatched(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        make_quick_sale(session, tenant_a, "70.00", day=TODAY, method=PaymentMethod.CASH)  # cash: not here
        session.commit()
        s = _summary(session, tenant_a)
        assert s.total_items == 1 and s.items[0].status is ReconStatus.UNMATCHED
        assert s.counts["UNMATCHED"] == 1 and s.amounts["UNMATCHED"] == D("100.00")

    def test_it_says_bank_integration_is_not_configured_and_invents_no_bank_rows(self, session, tenant_a):
        s = _summary(session, tenant_a)
        assert s.bank_integration == "Bank Integration Not Configured" and s.items == []
        assert any("Bank Integration Not Configured" in n for n in s.notes)

    def test_a_credit_sale_with_nothing_paid_is_not_a_payment_to_reconcile(self, session, tenant_a):
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        make_sale(
            session,
            tenant_a,
            "500.00",
            day=TODAY,
            paid="0.00",
            customer_id=c.id,
            method=PaymentMethod.UPI,
            cogs="1.00",
        )
        session.commit()
        assert _summary(session, tenant_a).total_items == 0

    def test_filters_by_status_and_method(self, session, tenant_a):
        ctx = context_for(tenant_a)
        s1 = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        make_quick_sale(session, tenant_a, "50.00", day=TODAY, method=PaymentMethod.OTHER)
        session.commit()
        reconciliation_service.mark(
            session, ctx, source_type="QUICK_SALE", source_id=s1.id, status=ReconStatus.MATCHED
        )
        session.commit()
        assert _summary(session, tenant_a, status=ReconStatus.MATCHED).total_items == 1
        assert _summary(session, tenant_a, payment_method="OTHER").total_items == 1


class TestMarking:
    def test_matched_confirms_the_full_amount_and_is_audited(self, session, tenant_a):
        ctx = context_for(tenant_a)
        q = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        m = reconciliation_service.mark(
            session,
            ctx,
            source_type="QUICK_SALE",
            source_id=q.id,
            status=ReconStatus.MATCHED,
            note="Seen in statement",
        )
        session.commit()
        assert m.confirmed_amount == D("100.00") and m.created_by == ctx.user_id
        assert "reconciliation_marked" in set(session.scalars(select(AuditLog.action)))
        assert _summary(session, tenant_a).items[0].status is ReconStatus.MATCHED

    def test_partial_must_be_less_and_review_needs_a_note(self, session, tenant_a):
        ctx = context_for(tenant_a)
        q = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        kw = {"source_type": "QUICK_SALE", "source_id": q.id}
        with pytest.raises(InvalidInputError):
            reconciliation_service.mark(
                session, ctx, status=ReconStatus.PARTIAL, confirmed_amount=D("100.00"), **kw
            )
        with pytest.raises(InvalidInputError):
            reconciliation_service.mark(session, ctx, status=ReconStatus.PARTIAL, **kw)
        with pytest.raises(InvalidInputError):
            reconciliation_service.mark(session, ctx, status=ReconStatus.REVIEW_REQUIRED, **kw)
        with pytest.raises(InvalidInputError):
            reconciliation_service.mark(
                session, ctx, status=ReconStatus.MATCHED, confirmed_amount=D("99.00"), **kw
            )
        ok = reconciliation_service.mark(
            session, ctx, status=ReconStatus.PARTIAL, confirmed_amount=D("60.00"), **kw
        )
        assert ok.confirmed_amount == D("60.00")

    def test_history_is_kept_and_the_newest_mark_is_the_status(self, session, tenant_a):
        ctx = context_for(tenant_a)
        q = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        kw = {"source_type": "QUICK_SALE", "source_id": q.id}
        reconciliation_service.mark(session, ctx, status=ReconStatus.MATCHED, **kw)
        reconciliation_service.mark(
            session, ctx, status=ReconStatus.REVIEW_REQUIRED, note="Amount differs", **kw
        )
        session.commit()
        assert _summary(session, tenant_a).items[0].status is ReconStatus.REVIEW_REQUIRED
        assert len(reconciliation_service.history(session, tenant_a.shop.id, "QUICK_SALE", q.id)) == 2

    def test_cash_and_unknown_records_cannot_be_marked(self, session, tenant_a):
        ctx = context_for(tenant_a)
        q = make_quick_sale(session, tenant_a, "10.00", day=TODAY, method=PaymentMethod.CASH)
        session.commit()
        with pytest.raises(InvalidInputError):
            reconciliation_service.mark(
                session, ctx, source_type="QUICK_SALE", source_id=q.id, status=ReconStatus.MATCHED
            )
        with pytest.raises(NotFoundError):
            reconciliation_service.mark(
                session, ctx, source_type="QUICK_SALE", source_id=99999, status=ReconStatus.MATCHED
            )
        with pytest.raises(NotFoundError):
            reconciliation_service.mark(
                session, ctx, source_type="BOGUS", source_id=1, status=ReconStatus.MATCHED
            )

    def test_marks_never_change_the_record_itself(self, session, tenant_a):
        ctx = context_for(tenant_a)
        q = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        reconciliation_service.mark(
            session, ctx, source_type="QUICK_SALE", source_id=q.id, status=ReconStatus.MATCHED
        )
        session.commit()
        session.refresh(q)
        assert (q.total_amount, q.amount_paid) == (D("100.00"), D("100.00"))

    def test_finance_entries_and_supplier_payments_are_reconcilable(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()
        e, _ = finance_ledger_service.record_entry(
            session,
            ctx,
            event_type=FinanceEventType.SUPPLIER_PAYMENT,
            direction=FlowDirection.OUT,
            amount=D("300.00"),
            payment_method=FinancePaymentMethod.BANK_TRANSFER,
            entry_date=TODAY,
            supplier_id=supplier.id,
        )
        session.commit()
        reconciliation_service.mark(
            session, ctx, source_type="FINANCE_ENTRY", source_id=e.id, status=ReconStatus.MATCHED
        )
        assert reconciliation_service.unreconciled_count(session, tenant_a.shop.id, TODAY, TODAY) == (
            0,
            D("0.00"),
        )


class TestApi:
    def test_mark_and_list_over_http_with_isolation_and_rbac(
        self, session, tenant_a, tenant_b, client_a, client_b, make_client
    ):
        from app.models.enums import UserRole

        q = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        body = {"source_type": "QUICK_SALE", "source_id": q.id, "status": "MATCHED"}
        assert client_a.post("/api/v1/finance/reconciliation/marks", json=body).status_code == 201
        listing = client_a.get(
            "/api/v1/finance/reconciliation", params={"date_from": (TODAY - timedelta(days=1)).isoformat()}
        ).json()
        assert (
            listing["counts"]["MATCHED"] == 1
            and listing["bank_integration"] == "Bank Integration Not Configured"
        )
        assert client_b.post("/api/v1/finance/reconciliation/marks", json=body).status_code == 404
        assert client_b.get("/api/v1/finance/reconciliation").json()["total_items"] == 0
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert staff.post("/api/v1/finance/reconciliation/marks", json=body).status_code == 403
