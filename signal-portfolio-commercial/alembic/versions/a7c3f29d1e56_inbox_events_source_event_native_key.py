"""inbox_events gets source_event_native_key

Revision ID: a7c3f29d1e56
Revises: a2c7e4f91b35
Create Date: 2026-10-01 00:00:00.000000

See app/models/integration_inbox.py's own docstring for what this
column is (Track 35: the stable native-provider identity a later
SourceEventKind.EDIT correlates back to its original event through,
app/services/integration_inbox.py's own `_source_event_native_key`).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c3f29d1e56'
down_revision: str | None = 'a2c7e4f91b35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('source_event_native_key', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_inbox_events_source_event_native_key'), 'inbox_events', ['source_event_native_key'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_inbox_events_source_event_native_key'), table_name='inbox_events')
    op.drop_column('inbox_events', 'source_event_native_key')
