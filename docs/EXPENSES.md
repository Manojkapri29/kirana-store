# Expenses

`DRAFT → SUBMITTED → APPROVED → POSTED`, plus `REJECTED` and `VOIDED`. Numbered `EXP/2026-27/0001` by the existing numbering
service. Fields: date, category, amount, payment method (CASH, UPI, CARD, BANK_TRANSFER, OTHER), payee, description, attachment
reference, notes, created/approved/posted by.

| Status | Affects reports? |
|---|---|
| DRAFT, SUBMITTED, APPROVED, REJECTED | **No** |
| POSTED | Yes (writes one ledger entry, idempotent per expense) |
| VOIDED | Its impact is reversed by a reversal entry; nothing is deleted |

* **Submit**: auto-approves unless the amount is at or above the shop's `expense_approval_threshold`, in which case it waits in
  the shared approval queue. The submitter can never approve it. Approve/reject through `/finance/expenses/{id}/approve|reject` or
  the shared `/approvals/{id}/decide` (needs `FINANCE_EXPENSE_APPROVE`); a rejection needs a reason.
* **Post**: only an APPROVED expense; refused if its date is in a locked/closed period; posting twice changes nothing.
* **Void**: needs a reason; writes the reversal (see the ledger's period rule).
* **Categories** are the shop's own (`/finance/expense-categories`); deactivating hides one from new expenses, old expenses keep it. A
  category may carry a cash-flow class (e.g. INVESTING).
