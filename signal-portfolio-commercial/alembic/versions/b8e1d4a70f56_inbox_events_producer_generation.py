"""inbox_events gets producer_generation, uniqueness scoped to it

Revision ID: b8e1d4a70f56
Revises: a1d4f7c9e203
Create Date: 2026-09-28 16:00:00.000000

See app/models/integration_inbox.py's own docstring for what this
column is (INT-010 "Producer restored to older database") and why the
uniqueness scope changes with it.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8e1d4a70f56'
down_revision: str | None = 'a1d4f7c9e203'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('producer_generation', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('inbox_events', 'producer_generation', server_default=None)
    op.drop_constraint('uq_inbox_events_stream_sequence', 'inbox_events', type_='unique')
    op.create_unique_constraint(
        'uq_inbox_events_stream_generation_sequence', 'inbox_events',
        ['source_stream', 'producer_generation', 'export_sequence'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_inbox_events_stream_generation_sequence', 'inbox_events', type_='unique')
    op.create_unique_constraint('uq_inbox_events_stream_sequence', 'inbox_events', ['source_stream', 'export_sequence'])
    op.drop_column('inbox_events', 'producer_generation')
