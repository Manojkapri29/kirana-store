"""Scheduled reports: each run is scoped to its own shop, idempotent (running due reports twice in a row does
nothing extra the second time), and stores a small structured summary rather than sending real email."""

from datetime import timedelta

from app.models.enums import ReportSchedule
from app.services import scheduled_report_service
from tests.conftest import context_for


class TestScheduledReports:
    def test_a_report_can_be_created_and_run_now(self, session, tenant_a):
        ctx = context_for(tenant_a)
        row = scheduled_report_service.create(
            session, ctx, report_type="sales_summary", schedule=ReportSchedule.DAILY
        )
        session.commit()

        ran = scheduled_report_service.run_now(session, ctx, row.id)
        session.commit()

        assert ran.last_status == "OK"
        assert ran.last_result is not None
        assert "net" in ran.last_result

    def test_run_due_only_picks_up_shops_reports_whose_time_has_come(
        self, session, tenant_a, tenant_b, session_factory
    ):
        ctx_a = context_for(tenant_a)
        ctx_b = context_for(tenant_b)
        due = scheduled_report_service.create(
            session, ctx_a, report_type="inventory_summary", schedule=ReportSchedule.DAILY
        )
        not_due = scheduled_report_service.create(
            session, ctx_b, report_type="inventory_summary", schedule=ReportSchedule.DAILY
        )
        session.commit()

        from app.db.types import utc_now
        from app.models import ScheduledReport

        due_row = session.get(ScheduledReport, due.id)
        due_row.next_run_at = utc_now() - timedelta(minutes=1)  # force it due
        session.commit()

        ran_count = scheduled_report_service.run_due(session)
        session.commit()

        session.refresh(due_row)
        not_due_row = session.get(ScheduledReport, not_due.id)
        session.refresh(not_due_row)
        assert ran_count == 1
        assert due_row.last_run_at is not None
        assert not_due_row.last_run_at is None

    def test_running_due_reports_twice_close_together_does_nothing_extra_the_second_time(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        row = scheduled_report_service.create(
            session, ctx, report_type="khata_summary", schedule=ReportSchedule.DAILY
        )
        session.commit()

        from app.db.types import utc_now
        from app.models import ScheduledReport

        stored = session.get(ScheduledReport, row.id)
        stored.next_run_at = utc_now() - timedelta(minutes=1)
        session.commit()

        first = scheduled_report_service.run_due(session)
        session.commit()
        second = scheduled_report_service.run_due(session)  # next_run_at has already moved forward
        session.commit()

        assert first == 1
        assert second == 0

    def test_an_unknown_report_type_is_refused_at_creation(self, session, tenant_a):
        import pytest

        from app.services.errors import InvalidInputError

        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            scheduled_report_service.create(
                session, ctx, report_type="not_a_real_report", schedule=ReportSchedule.DAILY
            )
