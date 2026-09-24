# Reporting and business intelligence

Reports are built in `app/reporting/` from posted documents only, with drill-down to the source rows. KPIs, cohorts and the report builder are defined in
[ANALYTICS.md](ANALYTICS.md), [ANALYTICS_KPIS.md](ANALYTICS_KPIS.md), [ANALYTICS_COHORTS.md](ANALYTICS_COHORTS.md), [REPORT_BUILDER.md](REPORT_BUILDER.md);
scheduled reports in [SCHEDULED_REPORTS.md](SCHEDULED_REPORTS.md); finance in [FINANCE.md](FINANCE.md), [PROFIT_AND_LOSS.md](PROFIT_AND_LOSS.md), [CASH_FLOW.md](CASH_FLOW.md).

Honesty rules enforced by tests: unknown cost is "Not Available" (never zero); a period with too little data says "Insufficient Data"; customer segments are
factual buckets, never inferred personal attributes; exports neutralise spreadsheet formula injection; the report builder accepts a validated allow-list definition, never SQL.
Emailing a scheduled report reports SENT / FAILED / NOT_CONFIGURED honestly. Performance numbers are in [PERFORMANCE.md](PERFORMANCE.md).
