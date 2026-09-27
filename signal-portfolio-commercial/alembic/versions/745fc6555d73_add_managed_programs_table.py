"""add managed_programs table

Revision ID: 745fc6555d73
Revises: b149fadfc60c
Create Date: 2026-09-28 02:45:00.000000

The `managed_programs` table (app/models/managed_program.py) backing
AD-14 "Managed-program setup". Plain tenant-scoped table, no compound
FK needed (this is admin config, not a customer's own record).

Applies row-level security in the SAME transaction this migration's own
`create_table` runs in (via `op.get_bind()`), pinned to only the table
THIS revision adds -- never the live, ever-growing
`app.db._TENANT_SCOPED_TABLES`, per `04c418cbb547`'s own lesson.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = '745fc6555d73'
down_revision: str | None = 'b149fadfc60c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'managed_programs',
        sa.Column('program_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('program_name', sa.String(), nullable=False),
        sa.Column('broker_program_id', sa.String(), nullable=False),
        sa.Column('mode', sa.Enum('PAMM', 'MAM', name='managedprogrammode', native_enum=False), nullable=False),
        sa.Column('allocation_policy_id', sa.String(), nullable=False),
        sa.Column('nav_policy_id', sa.String(), nullable=False),
        sa.Column('dealing_schedule_id', sa.String(), nullable=False),
        sa.Column('fee_policy_id', sa.String(), nullable=True),
        sa.Column('agreement_evidence_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            'state',
            sa.Enum('DRAFT', 'SUBMITTED_FOR_REVIEW', name='managedprogramstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('program_id'),
    )
    op.create_index(op.f('ix_managed_programs_tenant_id'), 'managed_programs', ['tenant_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("managed_programs",))


def downgrade() -> None:
    op.drop_index(op.f('ix_managed_programs_tenant_id'), table_name='managed_programs')
    op.drop_table('managed_programs')
