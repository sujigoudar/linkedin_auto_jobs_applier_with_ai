"""ledger_entries gets external_observation_id

Revision ID: a1d4f7c9e203
Revises: f3c8e2a5b716
Create Date: 2026-09-28 15:00:00.000000

See app/models/ledger.py's own docstring for what this column is
(S12 step 6 real FOLLOWER observation connector).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1d4f7c9e203'
down_revision: str | None = 'f3c8e2a5b716'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ledger_entries', sa.Column('external_observation_id', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_ledger_entries_external_observation_id'), 'ledger_entries', ['external_observation_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_external_observation_id'), table_name='ledger_entries')
    op.drop_column('ledger_entries', 'external_observation_id')
