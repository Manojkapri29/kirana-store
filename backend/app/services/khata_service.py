"""khata_service: the ONLY module allowed to write to `customer_ledger`. (Implemented in Phase 6.)

Same design as `inventory_service`: the customer balance is derived from an insert-only ledger, so all
rules about it live here. `tests/test_architecture.py` fails if another module writes to the ledger.

Planned responsibilities:
    - record OPENING_BALANCE, CREDIT_SALE, PAYMENT, RETURN_CREDIT, ADJUSTMENT and REVERSAL entries
    - answer "outstanding balance" queries and produce customer history

Transaction rule: this service never commits. The caller opens `app.db.session.write_transaction()`.
"""
