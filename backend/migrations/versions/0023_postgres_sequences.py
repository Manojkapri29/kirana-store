"""PostgreSQL: bring every id sequence up to the rows that were inserted with an explicit id

Earlier migrations seed rows (plans, units, roles, permissions, ...) with explicit ids. SQLite continues from the highest id by itself; PostgreSQL's
sequence does not, so the first row the application inserted afterwards collided with a seeded one (a duplicate primary key). This sets each table's
sequence to its current maximum id. It changes no data, does nothing on SQLite, and is safe to run again.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SET_SEQUENCES = """
DO $$
DECLARE
    t record;
    seq text;
BEGIN
    FOR t IN
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables x ON x.table_schema = c.table_schema AND x.table_name = c.table_name
        WHERE c.table_schema = current_schema() AND c.column_name = 'id' AND x.table_type = 'BASE TABLE'
          AND c.table_name <> 'alembic_version'
    LOOP
        seq := pg_get_serial_sequence(quote_ident(t.table_name), 'id');
        IF seq IS NOT NULL THEN
            EXECUTE format(
                'SELECT setval(%L, COALESCE((SELECT MAX(id) FROM %I), 1), (SELECT MAX(id) IS NOT NULL FROM %I))',
                seq, t.table_name, t.table_name
            );
        END IF;
    END LOOP;
END $$;
"""


def upgrade() -> None:
    if (
        op.get_bind().dialect.name == "postgresql"
    ):  # a plain SQL block, so it also renders with `alembic upgrade --sql`
        op.execute(SET_SEQUENCES)


def downgrade() -> None:
    """Nothing to undo: a sequence that is ahead of the data is harmless."""
