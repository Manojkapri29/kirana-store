"""Scheduled advanced reports: the last complete period, idempotent runs, honest delivery, creator permissions, existing
summary schedules untouched."""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.models import ReportRun, ScheduledReport
from app.models.enums import ReportSchedule
from app.services import advanced_report_service as svc
from app.services import scheduled_report_service
from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/scheduled-reports"


def _create(client, **body):
    return client.post(f"{API}/advanced", json={"kind": "adv_sales", "schedule": "DAILY", **body})


def _row(session, rid) -> ScheduledReport:
    return session.get(ScheduledReport, rid)


class TestPeriods:
    def test_daily_is_yesterday(self):
        p = svc.period_for(ReportSchedule.DAILY, date(2026, 9, 24))
        assert (p.start, p.end) == (date(2026, 9, 23), date(2026, 9, 23))

    def test_weekly_is_the_previous_monday_to_sunday(self):
        p = svc.period_for(ReportSchedule.WEEKLY, date(2026, 9, 24))  # a Thursday
        assert (p.start, p.end) == (date(2026, 9, 14), date(2026, 9, 20))
        on_monday = svc.period_for(ReportSchedule.WEEKLY, date(2026, 9, 21))
        assert (on_monday.start, on_monday.end) == (date(2026, 9, 14), date(2026, 9, 20))

    def test_monthly_is_the_previous_calendar_month_including_january(self):
        p = svc.period_for(ReportSchedule.MONTHLY, date(2026, 1, 5))
        assert (p.start, p.end) == (date(2025, 12, 1), date(2025, 12, 31))
        p = svc.period_for(ReportSchedule.MONTHLY, date(2026, 3, 1))
        assert (p.start, p.end) == (date(2026, 2, 1), date(2026, 2, 28))


class TestCreate:
    def test_creates_and_lists_with_delivery_settings(self, session, tenant_a, client_a):
        r = _create(client_a, export_format="pdf", delivery_channel="email", recipients=["A@Shop.in"])
        assert r.status_code == 201, r.text
        body = r.json()
        assert (
            body["export_format"] == "PDF"
            and body["delivery_channel"] == "EMAIL"
            and body["recipients"] == ["a@shop.in"]
        )
        assert any(x["id"] == body["id"] for x in client_a.get(API).json()["items"])

    def test_bad_input_is_refused(self, session, tenant_a, client_a):
        assert _create(client_a, kind="adv_nonsense").status_code == 422
        assert _create(client_a, export_format="DOCX").status_code == 422
        assert _create(client_a, delivery_channel="SMS", recipients=["a@b.in"]).status_code == 422
        assert _create(client_a, delivery_channel="EMAIL").status_code == 422  # no recipients
        assert _create(client_a, recipients=["a@b.in"]).status_code == 422  # recipients without a channel
        assert _create(client_a, delivery_channel="EMAIL", recipients=["not-an-email"]).status_code == 422
        assert _create(client_a, filters={"drop": "table"}).status_code == 422

    def test_needs_schedule_permission_and_the_reports_data_permission(self, session, tenant_a, make_client):
        no_sched = client_with(
            make_client, tenant_a, ["ANALYTICS_VIEW", "REPORT_VIEW", "SCHEDULED_REPORT_MANAGE"]
        )
        assert _create(no_sched).status_code == 403
        no_data = client_with(
            make_client, tenant_a, ["ANALYTICS_SCHEDULE", "SCHEDULED_REPORT_MANAGE", "ANALYTICS_ADVANCED"]
        )
        assert _create(no_data, kind="adv_finance").status_code == 403

    def test_saved_report_must_exist_in_this_shop(self, session, tenant_a, tenant_b, client_a, client_b):
        body = {"name": "S", "dataset": "sales", "definition": {}}
        rid = client_a.post("/api/v1/analytics/reports", json=body).json()["id"]
        assert _create(client_a, kind="saved_report", saved_report_id=rid).status_code == 201
        assert _create(client_b, kind="saved_report", saved_report_id=rid).status_code == 404


class TestRuns:
    def test_run_uses_the_last_complete_period_and_stores_a_summary(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY - timedelta(days=1))
        make_quick_sale(session, tenant_a, "999.00", day=TODAY)  # today is not a complete day
        session.commit()
        rid = _create(client_a).json()["id"]
        run = client_a.post(f"{API}/{rid}/run-now")
        assert run.status_code == 200
        (r,) = client_a.get(f"{API}/{rid}/runs").json()
        assert r["status"] == "OK" and r["period_end"] == (TODAY - timedelta(days=1)).isoformat()
        assert r["summary"]["combined_revenue"] == "80.00"
        assert r["delivery_status"] == "STORED_IN_APP"

    def test_running_twice_for_the_same_period_creates_one_run(self, session, tenant_a, client_a):
        rid = _create(client_a).json()["id"]
        for _ in range(3):
            assert client_a.post(f"{API}/{rid}/run-now").status_code == 200
        assert len(client_a.get(f"{API}/{rid}/runs").json()) == 1
        assert session.scalar(select(func.count()).select_from(ReportRun)) == 1

    def test_the_worker_and_run_now_do_not_duplicate(self, session, tenant_a, client_a):
        rid = _create(client_a).json()["id"]
        client_a.post(f"{API}/{rid}/run-now")
        row = _row(session, rid)
        row.next_run_at = row.next_run_at - timedelta(days=5)
        session.commit()
        scheduled_report_service.run_due(session)
        session.commit()
        assert session.scalar(select(func.count()).select_from(ReportRun)) == 1

    def test_email_is_never_reported_as_sent(self, session, tenant_a, client_a):
        rid = _create(client_a, delivery_channel="EMAIL", recipients=["a@shop.in"]).json()["id"]
        client_a.post(f"{API}/{rid}/run-now")
        (r,) = client_a.get(f"{API}/{rid}/runs").json()
        assert (
            r["delivery_status"] == "NOT_CONFIGURED"
            and r["summary"]["delivery"] == "Delivery Channel Not Configured"
        )

    def test_summary_holds_no_raw_rows_or_names(self, session, tenant_a, client_a):
        c = factories.make_customer(session, tenant_a.shop, name="Secret Name")
        session.commit()
        make_quick_sale(session, tenant_a, "80.00", day=TODAY - timedelta(days=1), customer_id=c.id)
        session.commit()
        for kind in ("adv_customers", "adv_cohorts", "adv_finance", "adv_inventory", "adv_kpis"):
            rid = _create(client_a, kind=kind, schedule="MONTHLY").json()["id"]
            client_a.post(f"{API}/{rid}/run-now")
        dump = str([r["summary"] for rid in range(1, 6) for r in client_a.get(f"{API}/{rid}/runs").json()])
        assert "Secret Name" not in dump

    def test_a_saved_report_runs_on_a_schedule(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY - timedelta(days=1))
        session.commit()
        body = {
            "name": "S",
            "dataset": "sales",
            "definition": {"aggregations": [{"field": "total_amount", "op": "SUM"}]},
        }
        sid = client_a.post("/api/v1/analytics/reports", json=body).json()["id"]
        rid = _create(client_a, kind="saved_report", saved_report_id=sid).json()["id"]
        client_a.post(f"{API}/{rid}/run-now")
        (r,) = client_a.get(f"{API}/{rid}/runs").json()
        assert r["status"] == "OK" and r["summary"]["totals"]["sum_total_amount"] == "80.00"

    def test_an_archived_saved_report_fails_honestly(self, session, tenant_a, client_a):
        sid = client_a.post(
            "/api/v1/analytics/reports", json={"name": "S", "dataset": "sales", "definition": {}}
        ).json()["id"]
        rid = _create(client_a, kind="saved_report", saved_report_id=sid).json()["id"]
        client_a.post(f"/api/v1/analytics/reports/{sid}/archive")
        client_a.post(f"{API}/{rid}/run-now")
        (r,) = client_a.get(f"{API}/{rid}/runs").json()
        assert r["status"] == "FAILED" and "archived" in r["error"] and r["summary"] is None

    def test_a_creator_who_lost_access_gets_a_failed_run_not_a_report(self, session, tenant_a, client_a):
        from app.models import User

        rid = _create(client_a).json()["id"]
        user = session.get(User, _row(session, rid).created_by)
        user.is_active = False
        session.commit()
        client_a.post(f"{API}/{rid}/run-now")
        (r,) = client_a.get(f"{API}/{rid}/runs").json()
        assert r["status"] == "FAILED" and "no longer has access" in r["error"]

    def test_runs_of_another_shop_are_not_visible(self, session, tenant_a, tenant_b, client_a, client_b):
        rid = _create(client_a).json()["id"]
        client_a.post(f"{API}/{rid}/run-now")
        assert client_b.get(f"{API}/{rid}/runs").status_code == 404

    def test_existing_summary_schedules_still_work(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        session.commit()
        r = client_a.post(API, json={"report_type": "sales_summary", "schedule": "DAILY"})
        assert r.status_code == 201
        run = client_a.post(f"{API}/{r.json()['id']}/run-now").json()
        assert run["last_status"] == "OK" and run["last_result"]["net"] is not None
        assert (
            client_a.get(f"{API}/{r.json()['id']}/runs").json() == []
        )  # summary schedules keep their own last_result


class TestFailedRunsAreRetried:
    def test_a_failed_period_is_tried_again_into_the_same_row_and_a_good_one_is_not_repeated(
        self, session, tenant_a, client_a
    ):
        from app.models import User

        rid = _create(client_a).json()["id"]
        user = session.get(User, _row(session, rid).created_by)
        user.is_active = False
        session.commit()
        client_a.post(f"{API}/{rid}/run-now")
        (failed,) = client_a.get(f"{API}/{rid}/runs").json()
        assert failed["status"] == "FAILED"
        user = session.get(User, _row(session, rid).created_by)
        user.is_active = True  # access is back
        session.commit()
        client_a.post(f"{API}/{rid}/run-now")
        (again,) = client_a.get(f"{API}/{rid}/runs").json()
        assert (
            again["id"] == failed["id"]
            and again["status"] == "OK"
            and again["error"] is None
            and again["summary"]
        )
        assert session.scalar(select(func.count()).select_from(ReportRun)) == 1
        client_a.post(f"{API}/{rid}/run-now")
        assert client_a.get(f"{API}/{rid}/runs").json() == [again]
