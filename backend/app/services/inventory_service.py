"""inventory_service: the ONLY module allowed to write to `inventory_transactions`. (Implemented in Phase 3.)

Why one writer: stock is derived from the ledger, so every rule about it (correct signs, no negative
stock, one transaction per business action, reversals instead of edits) must live in one place that can be
tested thoroughly. Purchases, sales, returns and adjustments all call this service; none of them touches
the table directly. `tests/test_architecture.py` fails if another module does.

Planned responsibilities:
    - record OPENING, PURCHASE, SALE, SALE_RETURN, PURCHASE_RETURN, ADJUSTMENT and REVERSAL rows
    - check available stock inside the same write_transaction as the insert
    - answer "current stock" queries for other services

Transaction rule: this service never commits. The caller opens `app.db.session.write_transaction()`.
"""
