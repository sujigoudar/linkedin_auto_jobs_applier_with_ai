"""add support_cases table

Revision ID: d11f8b0f0544
Revises: 858b08bda7d1
Create Date: 2026-09-27 23:15:00.000000

The `support_cases` table (app/models/support_case.py) backing CU-14
"Support and incident case". Plain tenant-scoped table, FK'd to
Membership like CustomerProfile/EligibilityAssessment's own precedent.

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
revision: str = 'd11f8b0f0544'
down_revision: str | None = '858b08bda7d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'support_cases',
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column(
            'category',
            sa.Enum(
                'BILLING', 'DELIVERY', 'CONNECTION', 'PERFORMANCE', 'SAFETY', 'ACCESS',
                name='supportcasecategory', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('related_object_id', sa.String(), nullable=True),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('attachment_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            'status',
            sa.Enum('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', name='supportcasestatus', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_support_case_membership',
        ),
        sa.PrimaryKeyConstraint('case_id'),
    )
    op.create_index(op.f('ix_support_cases_tenant_id'), 'support_cases', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_support_cases_user_id'), 'support_cases', ['user_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("support_cases",))


def downgrade() -> None:
    op.drop_index(op.f('ix_support_cases_user_id'), table_name='support_cases')
    op.drop_index(op.f('ix_support_cases_tenant_id'), table_name='support_cases')
    op.drop_table('support_cases')
