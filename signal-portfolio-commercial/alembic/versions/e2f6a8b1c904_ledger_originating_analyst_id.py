"""ledger_entries gets originating_analyst_id

Revision ID: e2f6a8b1c904
Revises: d7b1c5f4a930
Create Date: 2026-09-28 13:00:00.000000

See app/models/ledger.py's own docstring for what this column is
(INT-026 "Analyst allocation survives shared symbol").
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2f6a8b1c904'
down_revision: str | None = 'd7b1c5f4a930'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ledger_entries', sa.Column('originating_analyst_id', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_ledger_entries_originating_analyst_id'), 'ledger_entries', ['originating_analyst_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_originating_analyst_id'), table_name='ledger_entries')
    op.drop_column('ledger_entries', 'originating_analyst_id')
