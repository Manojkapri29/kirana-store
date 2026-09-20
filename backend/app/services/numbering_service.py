"""Document numbers: PUR/2026-27/0001.

Numbers are per shop, per kind of document, per financial year (April to March in India), and gapless
because the counter is incremented inside the same transaction that posts the document. If the posting rolls
back, so does the number, and the next document reuses it.

This is a table (`document_sequences`), not a database sequence, so it behaves identically on SQLite and
PostgreSQL. On SQLite the write transaction already serializes writers; on PostgreSQL the counter row is
locked with `SELECT ... FOR UPDATE` (ignored by SQLite).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DocumentSequence


def fiscal_year_label(on: date) -> str:
    """The Indian financial year containing `on`: 1 April 2026 to 31 March 2027 is "2026-27"."""
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def next_number(session: Session, shop_id: int, doc_type: str, fiscal_year: str) -> int:
    """Take the next number for this shop, document type and year. Call inside a write transaction."""
    query = select(DocumentSequence).where(
        DocumentSequence.shop_id == shop_id,
        DocumentSequence.doc_type == doc_type,
        DocumentSequence.fiscal_year == fiscal_year,
    )
    sequence = session.scalar(query.with_for_update())
    if sequence is None:
        sequence = DocumentSequence(
            shop_id=shop_id, doc_type=doc_type, fiscal_year=fiscal_year, last_number=0
        )
        try:
            with session.begin_nested():  # a rival transaction may create the row first; then just read it
                session.add(sequence)
                session.flush()
        except IntegrityError:
            sequence = session.scalars(query.with_for_update()).one()
    sequence.last_number += 1
    session.flush()
    return sequence.last_number


def format_document_number(prefix: str, fiscal_year: str, number: int) -> str:
    return f"{prefix}/{fiscal_year}/{number:04d}"
