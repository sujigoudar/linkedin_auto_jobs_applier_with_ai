"""ledger_entries gets follower_connection_id

Revision ID: 8b4e1c9d6a72
Revises: 3f7a19c02b8e
Create Date: 2026-09-28 10:00:00.000000

See app/models/ledger.py's own docstring on `follower_connection_id`
for what this column is (meaningful only for `book == Book.FOLLOWER`
entries) and why it has no FOREIGN KEY constraint to
platform_connections (a connection can be disconnected/removed later
without invalidating the historical fact that an entry was once
observed through it).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8b4e1c9d6a72'
down_revision: str | None = '3f7a19c02b8e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ledger_entries', sa.Column('follower_connection_id', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_ledger_entries_follower_connection_id'), 'ledger_entries', ['follower_connection_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_follower_connection_id'), table_name='ledger_entries')
    op.drop_column('ledger_entries', 'follower_connection_id')
