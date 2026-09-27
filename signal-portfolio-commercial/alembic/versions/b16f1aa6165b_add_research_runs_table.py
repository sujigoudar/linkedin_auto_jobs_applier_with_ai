"""add research_runs table

Revision ID: b16f1aa6165b
Revises: a5581417bf7f
Create Date: 2026-09-27 21:20:00.000000

The `research_runs` table (app/models/research_run.py) backing AD-04
"Portfolio Lab builder". Plain tenant-scoped table, added to app/db.py's
generic `_TENANT_SCOPED_TABLES`.

Applies row-level security to ONLY the table this revision adds (never
the live, ever-growing module constant) -- see `04c418cbb547`'s own
docstring for the real bug this guards against: an earlier migration
that defaults to the current tuple breaks on a fresh `alembic upgrade
head` replay once a later table is added to it.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'b16f1aa6165b'
down_revision: str | None = 'a5581417bf7f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'research_runs',
        sa.Column('research_run_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('sleeve_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('recipes', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('subset_min', sa.Integer(), nullable=False),
        sa.Column('subset_max', sa.Integer(), nullable=False),
        sa.Column('cash_bps', sa.Integer(), nullable=False),
        sa.Column('max_sleeve_bps', sa.Integer(), nullable=False),
        sa.Column('max_cluster_bps', sa.Integer(), nullable=False),
        sa.Column('train_sessions', sa.Integer(), nullable=False),
        sa.Column('test_sessions', sa.Integer(), nullable=False),
        sa.Column('holdout_fraction', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('cost_scenario_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('resource_profile_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('research_run_id'),
    )
    op.create_index(op.f('ix_research_runs_tenant_id'), 'research_runs', ['tenant_id'], unique=False)

    connection = op.get_bind()
    _apply_row_level_security(connection, ("research_runs",))


def downgrade() -> None:
    op.drop_index(op.f('ix_research_runs_tenant_id'), table_name='research_runs')
    op.drop_table('research_runs')
