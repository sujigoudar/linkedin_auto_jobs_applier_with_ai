"""inbox_events gets execution_correlation_key

Revision ID: d7b1c5f4a930
Revises: c4a9f0d3e812
Create Date: 2026-09-28 12:00:00.000000

See app/models/integration_inbox.py's own docstring for what this
column is (INT-012 "Late fee revises net report": the shared key a
later FEE event uses to find the EXECUTION_APPLIED row it corrects).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd7b1c5f4a930'
down_revision: str | None = 'c4a9f0d3e812'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('execution_correlation_key', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_inbox_events_execution_correlation_key'), 'inbox_events', ['execution_correlation_key'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_inbox_events_execution_correlation_key'), table_name='inbox_events')
    op.drop_column('inbox_events', 'execution_correlation_key')
