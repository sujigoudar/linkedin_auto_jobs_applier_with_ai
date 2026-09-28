"""real_account_routes + exclusive_ownership_plans (INT-033)

Revision ID: d1a5c7e93f02
Revises: c9f3a6d21e84
Create Date: 2026-09-28 16:30:00.000000

See app/models/real_account_route.py's own docstring for what these
tables are.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1a5c7e93f02'
down_revision: str | None = 'c9f3a6d21e84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'real_account_routes',
        sa.Column('broker', sa.String(), nullable=False),
        sa.Column('account_reference', sa.String(), nullable=False),
        sa.Column('channel', sa.String(), nullable=False),
        sa.Column('external_strategy_id', sa.String(), nullable=False),
        sa.Column('writer_identity', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('broker', 'account_reference'),
    )
    op.create_table(
        'exclusive_ownership_plans',
        sa.Column('broker', sa.String(), nullable=False),
        sa.Column('account_reference', sa.String(), nullable=False),
        sa.Column('approved_channel', sa.String(), nullable=False),
        sa.Column('approved_external_strategy_id', sa.String(), nullable=False),
        sa.Column('qualified_by', sa.String(), nullable=False),
        sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('broker', 'account_reference'),
    )


def downgrade() -> None:
    op.drop_table('exclusive_ownership_plans')
    op.drop_table('real_account_routes')
