# Cash management

    Expected closing cash = Opening cash
      + cash sales + customer payments + other cash income + owner capital + supplier refunds
      - cash purchases - expenses - supplier payments - refunds to customers - owner withdrawals
      +/- adjustments

Only **CASH** money that really moved counts (the settled part of a sale). A payment whose method was never recorded is **not**
assumed to be cash; it is listed as unclassified. `GET /finance/cash/summary?day=`.

**Opening cash is never guessed**: it is the *actual* cash of the latest physical count before the day, rolled forward by the cash
movements since. With no earlier count, opening and expected closing are **Not Available (null)**. The first count sets the baseline.

**Counts** (`POST /finance/cash/counts`, insert-only): store expected (from the books at that moment), actual, difference, reason,
counted by/at. A difference needs a reason. Expected cash is never edited. **Corrections** are explicit adjustments
(`/finance/adjustments`, `FINANCE_ADJUSTMENT_MANAGE`) with a reason and an audit row; above `cash_adjustment_threshold` they need a
second person's approval.
