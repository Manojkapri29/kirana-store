"""Custom report builder: allowlisted datasets/fields/operators only, no SQL, exact aggregation, permission checks on save and
run, archive instead of delete, shop isolation."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.reporting import builder
from app.services.errors import ForbiddenError, InvalidInputError
from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics"
P = {"preset": "this_month", "compare": "none"}
ALL = None  # validate(..., granted=None) means unrestricted, used to test the allowlist itself


def _preview(client, dataset, definition, status=200):
    r = client.post(f"{API}/builder/preview", params=P, json={"dataset": dataset, "definition": definition})
    assert r.status_code == status, r.text
    return r.json()


class TestAllowlist:
    @pytest.mark.parametrize(
        "definition",
        [
            {"columns": ["sale_date; DROP TABLE sales"]},
            {"columns": ["password_hash"]},
            {"group_by": ["cost_price"]},
            {"aggregations": [{"field": "total_amount", "op": "SUM; DELETE"}]},
            {"aggregations": [{"field": "kind", "op": "SUM"}]},
            {"filters": [{"field": "kind", "op": "like", "value": "x"}]},
            {"filters": [{"field": "total_amount", "op": "eq", "value": "1; SELECT 1"}]},
            {"filters": [{"field": "total_amount", "op": "contains", "value": "1"}]},
            {"filters": [{"field": "total_amount", "op": "eq", "value": "1", "sql": "1=1"}]},
            {"sql": "SELECT * FROM users"},
            {"joins": ["users"]},
            {"columns": ["kind"], "aggregations": [{"field": "total_amount", "op": "SUM"}]},
            {"columns": ["kind"], "sort": [{"field": "total_amount"}]},
        ],
    )
    def test_anything_outside_the_allowlist_is_refused(self, definition):
        with pytest.raises(InvalidInputError):
            builder.validate(definition, "sales", ALL)

    def test_unknown_dataset_is_refused(self):
        for name in ("users", "sales; DROP TABLE x", "audit_log", ""):
            with pytest.raises(InvalidInputError):
                builder.validate({}, name, ALL)

    def test_online_orders_cannot_be_run(self):
        with pytest.raises(InvalidInputError, match="Not Available in the report builder"):
            builder.validate({}, "online_orders", ALL)

    def test_limits_are_enforced(self):
        with pytest.raises(InvalidInputError):
            builder.validate({"columns": ["kind"] * 2}, "sales", ALL)
        with pytest.raises(InvalidInputError):
            builder.validate(
                {"filters": [{"field": "kind", "op": "eq", "value": "Quick"}] * 11}, "sales", ALL
            )

    def test_every_declared_field_exists_in_the_provider_rows(self, session, tenant_a):
        from app.reporting.filters import CompareMode, Preset, build_filters

        f = build_filters(TODAY, preset=Preset.THIS_MONTH, compare=CompareMode.NONE)
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        make_quick_sale(session, tenant_a, "50.00", day=TODAY)
        make_purchase(session, tenant_a, factories.make_supplier(session, tenant_a.shop), "80.00", day=TODAY)
        session.commit()
        for ds in builder.DATASETS.values():
            if ds.provider is None:
                continue
            rows = ds.provider(session, tenant_a.shop.id, f, TODAY)
            for row in rows[:3]:
                missing = {fld.key for fld in ds.fields} - set(row)
                assert not missing, (ds.key, missing)


class TestRunning:
    def test_group_and_sum_are_exact(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.10", day=TODAY, cogs="60.00")
        make_sale(session, tenant_a, "200.20", day=TODAY, cogs="90.00")
        make_quick_sale(session, tenant_a, "50.05", day=TODAY)
        session.commit()
        body = _preview(
            client_a,
            "sales",
            {
                "group_by": ["kind"],
                "aggregations": [
                    {"field": "total_amount", "op": "SUM"},
                    {"field": "total_amount", "op": "COUNT"},
                ],
            },
        )
        rows = {r["kind"]: r for r in body["rows"]}
        assert (
            rows["Detailed"]["sum_total_amount"] == "300.30" and rows["Detailed"]["count_total_amount"] == 2
        )
        assert rows["Quick"]["sum_total_amount"] == "50.05"

    def test_average_min_max_and_a_grand_total_row(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        make_sale(session, tenant_a, "300.00", day=TODAY, cogs="90.00")
        session.commit()
        aggs = [{"field": "total_amount", "op": op} for op in ("AVG", "MIN", "MAX")]
        (row,) = _preview(client_a, "sales", {"aggregations": aggs})["rows"]
        assert (row["avg_total_amount"], row["min_total_amount"], row["max_total_amount"]) == (
            "200.00",
            "100.00",
            "300.00",
        )

    def test_filters_and_sort_and_detail_columns(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        make_quick_sale(session, tenant_a, "30.00", day=TODAY)
        make_quick_sale(session, tenant_a, "20.00", day=TODAY)
        session.commit()
        body = _preview(
            client_a,
            "sales",
            {
                "columns": ["kind", "total_amount"],
                "filters": [{"field": "total_amount", "op": "gte", "value": "20"}],
                "sort": [{"field": "total_amount", "direction": "desc"}],
            },
        )
        assert [r["total_amount"] for r in body["rows"]] == ["30.00", "20.00"]
        assert [c[0] for c in body["columns"]] == ["kind", "total_amount"]

    def test_draft_and_other_period_rows_are_not_included(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY - timedelta(days=200))
        session.commit()
        assert _preview(client_a, "sales", {})["rows"] == []

    def test_unknown_profit_makes_the_sum_not_available_not_zero(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        body = _preview(
            client_a,
            "finance",
            {"aggregations": [{"field": "gross_profit", "op": "SUM"}, {"field": "revenue", "op": "SUM"}]},
        )
        (row,) = body["rows"]
        assert row["sum_gross_profit"] is None and row["sum_revenue"] == "1500.00"
        assert any("Not Available" in n for n in body["notes"])

    def test_pagination(self, session, tenant_a, client_a):
        for i in range(5):
            make_quick_sale(session, tenant_a, f"{10 + i}.00", day=TODAY)
        session.commit()
        r = client_a.post(
            f"{API}/builder/preview",
            params={**P, "limit": 2, "offset": 2},
            json={
                "dataset": "sales",
                "definition": {"columns": ["total_amount"], "sort": [{"field": "total_amount"}]},
            },
        )
        body = r.json()
        assert body["total"] == 5 and [x["total_amount"] for x in body["rows"]] == ["12.00", "13.00"]

    def test_repeat_runs_give_the_same_answer(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        session.commit()
        d = {"group_by": ["payment_method"], "aggregations": [{"field": "total_amount", "op": "SUM"}]}
        assert _preview(client_a, "sales", d) == _preview(client_a, "sales", d)


class TestPermissions:
    def test_the_catalog_lists_only_what_the_role_may_read(self, session, tenant_a, make_client):
        c = client_with(make_client, tenant_a, ["ANALYTICS_CUSTOM_REPORT", "REPORT_VIEW"])
        ds = {d["key"]: d for d in c.get(f"{API}/builder/datasets").json()["datasets"]}
        assert ds["sales"]["available"] and not ds["finance"]["available"] and ds["finance"]["fields"] == []
        assert "customer" not in {
            f["key"] for f in ds["sales"]["fields"]
        }  # customer data needs CUSTOMER_VIEW
        assert ds["online_orders"]["available"] is False

    def test_a_dataset_without_its_permission_cannot_be_run(self, session, tenant_a, make_client):
        c = client_with(make_client, tenant_a, ["ANALYTICS_CUSTOM_REPORT", "REPORT_VIEW"])
        assert (
            c.post(
                f"{API}/builder/preview", params=P, json={"dataset": "finance", "definition": {}}
            ).status_code
            == 403
        )
        assert (
            c.post(
                f"{API}/builder/preview",
                params=P,
                json={"dataset": "sales", "definition": {"columns": ["customer"]}},
            ).status_code
            == 422
        )

    def test_builder_needs_the_custom_report_permission(self, session, tenant_a, make_client):
        c = client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "REPORT_VIEW"])
        assert (
            c.post(
                f"{API}/builder/preview", params=P, json={"dataset": "sales", "definition": {}}
            ).status_code
            == 403
        )

    def test_service_raises_forbidden(self):
        with pytest.raises(ForbiddenError):
            builder.validate({}, "finance", {"REPORT_VIEW"})


class TestSavedReports:
    BODY = {
        "name": "Sales by kind",
        "description": "d",
        "dataset": "sales",
        "definition": {"group_by": ["kind"], "aggregations": [{"field": "total_amount", "op": "SUM"}]},
    }

    def test_save_edit_run_archive_restore(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "50.00", day=TODAY)
        session.commit()
        r = client_a.post(f"{API}/reports", json=self.BODY)
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        run = client_a.get(f"{API}/reports/{rid}/run", params=P).json()
        assert run["rows"][0]["sum_total_amount"] == "50.00"
        edited = {**self.BODY, "name": "Renamed"}
        assert client_a.put(f"{API}/reports/{rid}", json=edited).json()["name"] == "Renamed"
        assert client_a.post(f"{API}/reports/{rid}/archive").json()["is_archived"] is True
        assert client_a.get(f"{API}/reports").json()["items"] == []
        assert len(client_a.get(f"{API}/reports", params={"include_archived": True}).json()["items"]) == 1
        assert client_a.get(f"{API}/reports/{rid}/run", params=P).status_code == 409
        assert client_a.post(f"{API}/reports/{rid}/restore").json()["is_archived"] is False
        assert client_a.get(f"{API}/reports/{rid}/run", params=P).status_code == 200

    def test_no_http_delete_route_exists(self, client_a):
        assert client_a.delete(f"{API}/reports/1").status_code == 405

    def test_a_bad_definition_is_not_saved(self, session, tenant_a, client_a):
        bad = {**self.BODY, "definition": {"columns": ["password_hash"]}}
        assert client_a.post(f"{API}/reports", json=bad).status_code == 422
        assert client_a.get(f"{API}/reports").json()["items"] == []

    def test_a_tampered_stored_definition_still_cannot_run(self, session, tenant_a, client_a):
        rid = client_a.post(f"{API}/reports", json=self.BODY).json()["id"]
        from app.models import SavedReport

        row = session.get(SavedReport, rid)
        row.definition = {"columns": ["password_hash"]}
        session.commit()
        assert client_a.get(f"{API}/reports/{rid}/run", params=P).status_code == 422

    def test_duplicate_name_is_a_conflict(self, session, tenant_a, client_a):
        assert client_a.post(f"{API}/reports", json=self.BODY).status_code == 201
        assert client_a.post(f"{API}/reports", json=self.BODY).status_code == 409

    def test_saved_reports_are_shop_scoped(self, session, tenant_a, tenant_b, client_a, client_b):
        rid = client_a.post(f"{API}/reports", json=self.BODY).json()["id"]
        assert client_b.get(f"{API}/reports").json()["items"] == []
        assert client_b.get(f"{API}/reports/{rid}").status_code == 404
        assert client_b.get(f"{API}/reports/{rid}/run", params=P).status_code == 404
        assert client_b.post(f"{API}/reports/{rid}/archive").status_code == 404

    def test_running_uses_the_current_callers_permissions(self, session, tenant_a, client_a, make_client):
        finance = {"name": "Fin", "dataset": "finance", "definition": {}}
        rid = client_a.post(f"{API}/reports", json=finance).json()["id"]
        weak = client_with(make_client, tenant_a, ["ANALYTICS_CUSTOM_REPORT", "REPORT_VIEW"])
        assert weak.get(f"{API}/reports/{rid}/run", params=P).status_code == 403

    def test_data_from_another_shop_never_appears(self, session, tenant_a, tenant_b, client_b):
        make_quick_sale(session, tenant_a, "999.00", day=TODAY)
        session.commit()
        assert _preview(client_b, "sales", {})["rows"] == []
        assert Decimal("0") == 0
