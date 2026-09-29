"""inbox_events gets routing_outcome

Revision ID: f4b2c8e0a913
Revises: e9a3c6f1d208
Create Date: 2026-09-29 00:00:00.000000

See app/models/integration_inbox.py's own docstring for what this
column is (INT-027 "All permitted source outcomes reach research": the
real routing/admission/fill outcome a later, correlated
ROUTING_ADMISSION_OUTCOME event applies onto its originating
SOURCE_RECEIPT row).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4b2c8e0a913'
down_revision: str | None = 'e9a3c6f1d208'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('inbox_events', sa.Column('routing_outcome', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('inbox_events', 'routing_outcome')
