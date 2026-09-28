"""inbox_events gets parked_reason

Revision ID: c4a9f0d3e812
Revises: 8b4e1c9d6a72
Create Date: 2026-09-28 11:00:00.000000

See app/models/integration_inbox.py's own docstring for what this
column is (INT-007 "Unsupported schema version": an unapplied row must
say WHY, not just that it's unapplied) and app/services/
integration_inbox.py's `_apply_projection` for the two prefixes it's
ever set to.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4a9f0d3e812'
down_revision: str | None = '8b4e1c9d6a72'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('parked_reason', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('inbox_events', 'parked_reason')
