"""The analytics documentation must not drift from the code: every KPI is documented with its real formula and source, the API table is
the route table, and the files the docs point at exist."""

import re
from pathlib import Path

from app.core.permissions import ROUTE_RULES
from app.reporting import kpis

DOCS = Path(__file__).resolve().parents[2] / "docs"
README = Path(__file__).resolve().parents[2] / "README.md"


def _api_table() -> str:
    rows = [
        r
        for r in ROUTE_RULES
        if (r.template.startswith("/analytics") and r.template != "/analytics/overview")
        or r.template in ("/scheduled-reports/advanced", "/scheduled-reports/{report_id}/runs")
    ]
    lines = ["| Method | Path (under `/api/v1`) | Permissions (all needed) |", "| --- | --- | --- |"]
    lines += [f"| {r.method} | `{r.template}` | {', '.join(f'`{p}`' for p in r.needs)} |" for r in rows]
    return "\n".join(lines)


def test_every_kpi_is_documented_with_its_formula_source_and_permission():
    text = (DOCS / "ANALYTICS_KPIS.md").read_text()
    for key, (d, _fn) in kpis.KPIS.items():
        row = next((line for line in text.splitlines() if f"(`{key}`)" in line), None)
        assert row is not None, f"KPI {key} is not documented"
        for part in (d.name, d.formula.replace("|", "\\|"), d.source.replace("|", "\\|"), d.permission):
            assert part in row, (key, part)


def test_the_api_table_is_the_route_table():
    text = (DOCS / "ANALYTICS.md").read_text()
    block = re.search(r"<!-- API-TABLE -->\n(.*?)\n<!-- /API-TABLE -->", text, re.S)
    assert block is not None and block.group(1).strip() == _api_table().strip()


def test_documented_files_exist_and_are_linked_from_the_readme():
    readme = README.read_text()
    for name in (
        "ANALYTICS.md",
        "ANALYTICS_KPIS.md",
        "ANALYTICS_COHORTS.md",
        "REPORT_BUILDER.md",
        "ANALYTICS_AI_TOOLS.md",
    ):
        assert (DOCS / name).exists(), name
        assert f"docs/{name}" in readme, name


def test_every_documented_analytics_permission_exists():
    from app.core.permissions import PERMISSIONS

    text = (DOCS / "ANALYTICS.md").read_text()
    for code in set(re.findall(r"`(ANALYTICS_[A-Z_]+)`", text)):
        assert code in PERMISSIONS, code


def test_the_measured_numbers_in_the_docs_come_from_the_dataset_the_test_seeds():
    from tests import test_phase16_performance as perf

    text = (DOCS / "ANALYTICS.md").read_text()
    for number in (
        f"{perf.PRODUCTS:,}",
        f"{perf.CUSTOMERS:,}",
        f"{perf.SALES:,}",
        f"{perf.QUICK:,}",
        str(perf.PURCHASES),
    ):
        assert number in text, number
