# Reconciliation foundation

There is **no bank integration and no bank data**; the report says "Bank Integration Not Configured" and nothing is matched
automatically or invented. What it offers: a reviewed status per **electronic** payment record (non-cash money that really moved,
including customer/supplier payments and electronic refunds), set by an authorised person after checking their own statement.

Statuses: `UNMATCHED` (default), `MATCHED` (confirmed in full), `PARTIAL` (confirmed for less), `REVIEW_REQUIRED` (needs a note).
Marks are insert-only history (`reconciliation_marks`); the newest is current; each is audited. Cash is not reconciled here (cash
counts cover it). `GET /finance/reconciliation`, `POST /finance/reconciliation/marks` (`FINANCE_RECONCILIATION_MANAGE`).
