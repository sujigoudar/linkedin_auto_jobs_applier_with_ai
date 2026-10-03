"""publication_intents gets side (G-C-02)

Revision ID: wp39_0001
Revises: f6f6e1fc0793
Create Date: 2026-10-02 20:00:00.000000

G-C-02 fix: add required `side` field to PublicationIntent so adapters use
it instead of hard-coding BUY for OPEN/ADD or inferring from action alone.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'wp39_0001'
down_revision: str | None = 'c7e2f91a4d05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'publication_intents',
        sa.Column('side', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('publication_intents', 'side')
