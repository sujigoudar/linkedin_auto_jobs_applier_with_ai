"""add integration_configurations table

Revision ID: 38c97c922bb0
Revises: 86f112546ee3
Create Date: 2026-09-28 01:00:00.000000

The `integration_configurations` table (app/models/integration_configuration.py)
backing AD-17 "Integrations, data rights and quotas". Plain tenant-
scoped table, no compound FK needed (this is admin config, not a
customer's own record).

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
revision: str = '38c97c922bb0'
down_revision: str | None = '86f112546ee3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'integration_configurations',
        sa.Column('integration_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('provider_registry_id', sa.String(), nullable=False),
        sa.Column(
            'purpose',
            sa.Enum(
                'RESEARCH', 'QUOTES', 'REFERENCE', 'PUBLICATION', 'BILLING', 'MONITORING',
                name='integrationpurpose', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'environment',
            sa.Enum('TEST', 'DEMO', 'LIVE', name='integrationenvironment', native_enum=False),
            nullable=False,
        ),
        sa.Column('credential_ref', sa.String(), nullable=True),
        sa.Column('entitlement_evidence_id', sa.String(), nullable=True),
        sa.Column('quota_profile_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('integration_id'),
    )
    op.create_index(
        op.f('ix_integration_configurations_tenant_id'), 'integration_configurations', ['tenant_id'], unique=False
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("integration_configurations",))


def downgrade() -> None:
    op.drop_index(op.f('ix_integration_configurations_tenant_id'), table_name='integration_configurations')
    op.drop_table('integration_configurations')
