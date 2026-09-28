"""add platform_connections table

Revision ID: a8ec01b2f2cd
Revises: 78d1a8daff8a
Create Date: 2026-09-28 00:10:00.000000

The `platform_connections` table (app/models/platform_connection.py)
backing CU-07 "Platform connections" / CU-08 "Connection wizard".

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
revision: str = 'a8ec01b2f2cd'
down_revision: str | None = '78d1a8daff8a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'platform_connections',
        sa.Column('connection_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('environment', sa.String(), nullable=False),
        sa.Column('masked_account_label', sa.String(), nullable=False),
        sa.Column(
            'state', sa.Enum('DECLARED', 'DISCONNECTED', name='platformconnectionstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_platform_connection_membership',
        ),
        sa.PrimaryKeyConstraint('connection_id'),
    )
    op.create_index(op.f('ix_platform_connections_tenant_id'), 'platform_connections', ['tenant_id'])
    op.create_index(op.f('ix_platform_connections_user_id'), 'platform_connections', ['user_id'])

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("platform_connections",))


def downgrade() -> None:
    op.drop_index(op.f('ix_platform_connections_user_id'), table_name='platform_connections')
    op.drop_index(op.f('ix_platform_connections_tenant_id'), table_name='platform_connections')
    op.drop_table('platform_connections')
