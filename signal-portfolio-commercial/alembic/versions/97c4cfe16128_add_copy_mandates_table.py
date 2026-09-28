"""add copy_mandates table

Revision ID: 97c4cfe16128
Revises: a8ec01b2f2cd
Create Date: 2026-09-28 00:15:00.000000

The `copy_mandates` table (app/models/copy_mandate.py) backing CU-09
"Copy setup and mandate wizard" -- closes the "no copy-mandate model
exists at all" gap AD-11's own slice already documented.

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
revision: str = '97c4cfe16128'
down_revision: str | None = 'a8ec01b2f2cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'copy_mandates',
        sa.Column('mandate_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('selection_id', sa.String(), nullable=False),
        sa.Column('connection_id', sa.String(), nullable=False),
        sa.Column('allocation_amount', sa.Numeric(28, 10), nullable=False),
        sa.Column('allocation_currency', sa.String(), nullable=False),
        sa.Column('max_trade_risk', sa.Numeric(28, 10), nullable=True),
        sa.Column('max_loss', sa.Numeric(28, 10), nullable=True),
        sa.Column(
            'start_mode',
            sa.Enum('NEW_ENTRIES_ONLY', 'SYNC_EXISTING', name='copymandatestartmode', native_enum=False),
            nullable=False,
        ),
        sa.Column('policy_version_id', sa.String(), nullable=False),
        sa.Column('consent_version', sa.String(), nullable=False),
        sa.Column('state', sa.Enum('DRAFT', 'CANCELLED', name='copymandatestate', native_enum=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_copy_mandate_membership',
        ),
        sa.PrimaryKeyConstraint('mandate_id'),
    )
    op.create_index(op.f('ix_copy_mandates_tenant_id'), 'copy_mandates', ['tenant_id'])
    op.create_index(op.f('ix_copy_mandates_user_id'), 'copy_mandates', ['user_id'])

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("copy_mandates",))


def downgrade() -> None:
    op.drop_index(op.f('ix_copy_mandates_user_id'), table_name='copy_mandates')
    op.drop_index(op.f('ix_copy_mandates_tenant_id'), table_name='copy_mandates')
    op.drop_table('copy_mandates')
