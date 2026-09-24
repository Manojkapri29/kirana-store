"""Business health: current period vs. the previous comparable one, "not enough historical data" instead of a
fabricated change, and never an accusation of fraud."""

from app.services import business_health_service as bhs
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()


class TestComparePeriods:
    def test_a_brand_new_shop_with_no_prior_period_says_not_enough_data_not_zero_percent(
        self, session, tenant_a
    ):
        current = (TODAY, TODAY)
        previous = (TODAY.replace(day=1), TODAY.replace(day=1))

        metrics = bhs.compare_periods(session, tenant_a.shop.id, current, previous)

        sales_metric = next(m for m in metrics if m.label == "Net sales")
        assert sales_metric.note == bhs.NOT_ENOUGH_DATA
        assert sales_metric.previous is None
        assert sales_metric.change_percent is None

    def test_health_report_never_contains_the_word_fraud(self, session, tenant_a):
        report = bhs.health_report(session, tenant_a.shop.id, TODAY)

        for metric in report.metrics:
            assert "fraud" not in metric.label.lower()
            assert metric.note is None or "fraud" not in metric.note.lower()
        for anomaly in report.anomalies:
            assert "fraud" not in anomaly.title.lower()
            assert "fraud" not in anomaly.detail.lower()

    def test_the_report_period_labels_describe_the_configured_window(self, session, tenant_a):
        report = bhs.health_report(session, tenant_a.shop.id, TODAY, days=14)

        assert report.period_label
        assert report.previous_label
        assert report.period_label != report.previous_label
