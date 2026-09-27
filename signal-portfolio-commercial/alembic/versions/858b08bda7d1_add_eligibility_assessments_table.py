"""add eligibility_assessments table

Revision ID: 858b08bda7d1
Revises: b16f1aa6165b
Create Date: 2026-09-27 22:30:00.000000

The `eligibility_assessments` table (app/models/eligibility.py) backing
ID-04 "Service eligibility onboarding". Plain tenant-scoped table with
a compound (tenant_id, user_id) primary key, matching CustomerProfile's
own FK-to-membership precedent.

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
revision: str = '858b08bda7d1'
down_revision: str | None = 'b16f1aa6165b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'eligibility_assessments',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('residence_country', sa.String(), nullable=False),
        sa.Column('tax_residence', sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            'customer_type',
            sa.Enum('INDIVIDUAL', 'ENTITY', name='customertype', native_enum=False),
            nullable=False,
        ),
        sa.Column('requested_service_modes', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('document_versions', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('facts_confirmed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_eligibility_assessment_membership',
        ),
        sa.PrimaryKeyConstraint('tenant_id', 'user_id'),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("eligibility_assessments",))


def downgrade() -> None:
    op.drop_table('eligibility_assessments')
