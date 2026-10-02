"""ledger_entries gets account_id (G-C-12)

Revision ID: wp39_0002
Revises: wp39_0001
Create Date: 2026-10-02 20:00:00.000000

G-C-12 fix: add account_id to LedgerEntry to track which copier account
each PLATFORM book fill came from. Enables per-account P&L replays instead
of collapsing all accounts into one position per instrument.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'wp39_0002'
down_revision: str | None = 'wp39_0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ledger_entries', sa.Column('account_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_ledger_entries_account_id'), 'ledger_entries', ['account_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_account_id'), table_name='ledger_entries')
    op.drop_column('ledger_entries', 'account_id')
