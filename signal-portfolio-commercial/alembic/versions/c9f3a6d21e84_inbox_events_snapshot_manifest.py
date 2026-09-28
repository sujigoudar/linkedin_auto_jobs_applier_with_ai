"""inbox_events gets manifest/snapshot columns

Revision ID: c9f3a6d21e84
Revises: b8e1d4a70f56
Create Date: 2026-09-28 17:00:00.000000

See app/models/integration_inbox.py's own docstring for what these
columns are (INT-008/INT-009 "Snapshot and delta overlap"/"Interrupted
bootstrap resumes").
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9f3a6d21e84'
down_revision: str | None = 'b8e1d4a70f56'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('manifest_id', sa.String(), nullable=True))
    op.add_column('inbox_events', sa.Column('snapshot_page_index', sa.Integer(), nullable=True))
    op.add_column('inbox_events', sa.Column('snapshot_page_count', sa.Integer(), nullable=True))
    op.add_column('inbox_events', sa.Column('snapshot_cutoff_sequence', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_inbox_events_manifest_id'), 'inbox_events', ['manifest_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inbox_events_manifest_id'), table_name='inbox_events')
    op.drop_column('inbox_events', 'snapshot_cutoff_sequence')
    op.drop_column('inbox_events', 'snapshot_page_count')
    op.drop_column('inbox_events', 'snapshot_page_index')
    op.drop_column('inbox_events', 'manifest_id')
