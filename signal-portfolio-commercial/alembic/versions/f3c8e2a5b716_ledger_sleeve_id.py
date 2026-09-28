"""ledger_entries gets sleeve_id

Revision ID: f3c8e2a5b716
Revises: e2f6a8b1c904
Create Date: 2026-09-28 14:00:00.000000

See app/models/ledger.py's own docstring for what this column is
(S12 step 5 "Portfolio Lab source feed").
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3c8e2a5b716'
down_revision: str | None = 'e2f6a8b1c904'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ledger_entries', sa.Column('sleeve_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_ledger_entries_sleeve_id'), 'ledger_entries', ['sleeve_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_sleeve_id'), table_name='ledger_entries')
    op.drop_column('ledger_entries', 'sleeve_id')
