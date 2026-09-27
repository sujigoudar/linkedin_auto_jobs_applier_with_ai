"""add workspace_settings table

Revision ID: 390949c83081
Revises: b36eae6aeb63
Create Date: 2026-09-28 03:45:00.000000

The `workspace_settings` table (app/models/workspace_settings.py)
backing AD-20 "Workspace customization and configuration". One row
per tenant, like CustomerProfile's own precedent -- no compound FK
needed (this is the tenant's own shared admin draft, not a customer's
own record).

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
revision: str = '390949c83081'
down_revision: str | None = 'b36eae6aeb63'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'workspace_settings',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('workspace_name', sa.String(), nullable=False),
        sa.Column('theme', sa.Enum('SYSTEM', 'DARK', 'LIGHT', name='workspacetheme', native_enum=False), nullable=False),
        sa.Column(
            'density', sa.Enum('COMFORTABLE', 'COMPACT', name='workspacedensity', native_enum=False), nullable=False
        ),
        sa.Column('visible_panel_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('column_order', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('notification_route_id', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id'),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("workspace_settings",))


def downgrade() -> None:
    op.drop_table('workspace_settings')
