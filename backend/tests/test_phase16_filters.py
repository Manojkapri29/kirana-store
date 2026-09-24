"""Report periods, presets, comparison periods and pagination limits: pure date arithmetic, no database."""

from datetime import date

import pytest

from app.reporting.filters import (
    CompareMode,
    Preset,
    build_filters,
    comparison_for,
    resolve_preset,
)
from app.services.errors import InvalidInputError

WED = date(2026, 9, 16)  # a Wednesday, in the third quarter, a 30-day month


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    [
        (Preset.TODAY, date(2026, 9, 16), date(2026, 9, 16)),
        (Preset.YESTERDAY, date(2026, 9, 15), date(2026, 9, 15)),
        (Preset.THIS_WEEK, date(2026, 9, 14), date(2026, 9, 16)),  # Monday to today
        (Preset.LAST_WEEK, date(2026, 9, 7), date(2026, 9, 13)),
        (Preset.THIS_MONTH, date(2026, 9, 1), date(2026, 9, 16)),
        (Preset.LAST_MONTH, date(2026, 8, 1), date(2026, 8, 31)),
        (Preset.THIS_QUARTER, date(2026, 7, 1), date(2026, 9, 16)),
        (Preset.LAST_QUARTER, date(2026, 4, 1), date(2026, 6, 30)),
        (Preset.THIS_YEAR, date(2026, 1, 1), date(2026, 9, 16)),
        (Preset.LAST_YEAR, date(2025, 1, 1), date(2025, 12, 31)),
    ],
)
def test_every_preset_resolves_to_the_expected_dates(preset, start, end):
    p = resolve_preset(preset, WED)
    assert (p.start, p.end) == (start, end)


def test_the_year_boundary_for_last_month_and_last_quarter():
    jan = date(2026, 1, 10)
    assert (resolve_preset(Preset.LAST_MONTH, jan).start, resolve_preset(Preset.LAST_MONTH, jan).end) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
    )
    q = resolve_preset(Preset.LAST_QUARTER, jan)
    assert (q.start, q.end) == (date(2025, 10, 1), date(2025, 12, 31))


def test_leap_day_and_month_lengths():
    assert resolve_preset(Preset.LAST_MONTH, date(2024, 3, 5)).end == date(2024, 2, 29)


def test_a_month_to_date_compares_the_same_elapsed_days_of_last_month():
    p = resolve_preset(Preset.THIS_MONTH, WED)
    c = comparison_for(Preset.THIS_MONTH, p, CompareMode.PREVIOUS_PERIOD)
    assert (c.start, c.end) == (date(2026, 8, 1), date(2026, 8, 16))  # 16 days against 16 days


def test_the_comparison_is_clipped_to_the_shorter_previous_month():
    p = resolve_preset(Preset.THIS_MONTH, date(2026, 3, 31))
    c = comparison_for(Preset.THIS_MONTH, p, CompareMode.PREVIOUS_PERIOD)
    assert (c.start, c.end) == (date(2026, 2, 1), date(2026, 2, 28))


def test_week_quarter_and_year_to_date_comparisons():
    w = comparison_for(Preset.THIS_WEEK, resolve_preset(Preset.THIS_WEEK, WED), CompareMode.PREVIOUS_PERIOD)
    assert (w.start, w.end) == (date(2026, 9, 7), date(2026, 9, 9))
    q = comparison_for(
        Preset.THIS_QUARTER, resolve_preset(Preset.THIS_QUARTER, WED), CompareMode.PREVIOUS_PERIOD
    )
    assert q.start == date(2026, 4, 1)
    assert (q.end - q.start).days + 1 == (WED - date(2026, 7, 1)).days + 1
    y = comparison_for(Preset.THIS_YEAR, resolve_preset(Preset.THIS_YEAR, WED), CompareMode.PREVIOUS_PERIOD)
    assert (y.start, (y.end - y.start).days) == (date(2025, 1, 1), (WED - date(2026, 1, 1)).days)


def test_other_presets_compare_with_the_equal_length_window_before():
    p = resolve_preset(Preset.LAST_MONTH, WED)  # August: 31 days
    c = comparison_for(Preset.LAST_MONTH, p, CompareMode.PREVIOUS_PERIOD)
    assert (c.start, c.end) == (date(2026, 7, 1), date(2026, 7, 31))
    t = comparison_for(Preset.TODAY, resolve_preset(Preset.TODAY, WED), CompareMode.PREVIOUS_PERIOD)
    assert (t.start, t.end) == (date(2026, 9, 15), date(2026, 9, 15))
    custom = build_filters(WED, date_from=date(2026, 9, 1), date_to=date(2026, 9, 10))
    assert (custom.comparison.start, custom.comparison.end) == (date(2026, 8, 22), date(2026, 8, 31))


def test_previous_year_and_no_comparison():
    p = resolve_preset(Preset.THIS_MONTH, WED)
    c = comparison_for(Preset.THIS_MONTH, p, CompareMode.PREVIOUS_YEAR)
    assert (c.start, c.end) == (date(2025, 9, 1), date(2025, 9, 16))
    assert comparison_for(Preset.THIS_MONTH, p, CompareMode.NONE) is None
    leap = comparison_for(
        Preset.CUSTOM,
        resolve_preset(Preset.CUSTOM, WED, date(2028, 2, 29), date(2028, 2, 29)),
        CompareMode.PREVIOUS_YEAR,
    )
    assert leap.start == date(2027, 2, 28)


def test_an_explicit_comparison_range_wins_and_needs_both_dates():
    f = build_filters(
        WED, preset=Preset.THIS_MONTH, compare_from=date(2026, 1, 1), compare_to=date(2026, 1, 16)
    )
    assert f.comparison.start == date(2026, 1, 1)
    with pytest.raises(InvalidInputError):
        build_filters(WED, preset=Preset.THIS_MONTH, compare_from=date(2026, 1, 1))


def test_bad_periods_and_limits_are_refused():
    with pytest.raises(InvalidInputError):
        build_filters(WED, date_from=date(2026, 9, 10), date_to=date(2026, 9, 1))
    with pytest.raises(InvalidInputError):
        resolve_preset(Preset.CUSTOM, WED)
    with pytest.raises(InvalidInputError):
        build_filters(WED, limit=10_000)
    with pytest.raises(InvalidInputError):
        build_filters(WED, offset=-1)
    with pytest.raises(InvalidInputError):
        build_filters(WED, date_from=date(2000, 1, 1), date_to=date(2026, 1, 1))


def test_a_filter_a_report_cannot_honour_is_reported_as_ignored_not_dropped_silently():
    f = build_filters(WED, preset=Preset.THIS_MONTH, supplier_id=3, payment_method="UPI")
    effect = f.effect({"payment_method"})
    assert effect.applied == ["payment_method"] and effect.ignored == ["supplier_id"]
