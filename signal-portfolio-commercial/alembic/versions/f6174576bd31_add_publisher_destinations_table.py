"""add publisher_destinations table

Revision ID: f6174576bd31
Revises: d11f8b0f0544
Create Date: 2026-09-27 23:45:00.000000

The `publisher_destinations` table (app/models/publisher_destination.py)
backing AD-09 "Publisher channels and strategies". Plain tenant-scoped
table, no compound FK to Membership needed (this is admin config, not a
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
revision: str = 'f6174576bd31'
down_revision: str | None = 'd11f8b0f0544'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'publisher_destinations',
        sa.Column('destination_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column(
            'platform',
            sa.Enum('COLLECTIVE2', 'ETORO', 'COPYFACTORY', 'BROKER_NATIVE', name='platform', native_enum=False),
            nullable=False,
        ),
        sa.Column('external_strategy_id', sa.String(), nullable=False),
        sa.Column(
            'environment',
            sa.Enum(
                'LOCAL_SIMULATION', 'EXTERNAL_TEST', 'DEMO', 'LIVE',
                name='publisherenvironment', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('credential_ref', sa.String(), nullable=True),
        sa.Column('capability_manifest_id', sa.String(), nullable=True),
        sa.Column(
            'publication_mode',
            sa.Enum(
                'API_STRATEGY_PUBLISHER', 'APPROVED_MASTER_COPY',
                name='publicationmode', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('destination_id'),
    )
    op.create_index(op.f('ix_publisher_destinations_tenant_id'), 'publisher_destinations', ['tenant_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("publisher_destinations",))


def downgrade() -> None:
    op.drop_index(op.f('ix_publisher_destinations_tenant_id'), table_name='publisher_destinations')
    op.drop_table('publisher_destinations')
