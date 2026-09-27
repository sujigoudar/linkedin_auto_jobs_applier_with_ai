"""add customer_display_preferences table

Revision ID: 78d1a8daff8a
Revises: c020a36382f4
Create Date: 2026-09-27 23:40:00.000000

The `customer_display_preferences` table
(app/models/customer_display_preferences.py) backing CU-13 "Profile,
security and display preferences"'s Profile/Display steps. One row per
(tenant_id, user_id), like `NotificationPreferences`'s own precedent.

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
revision: str = '78d1a8daff8a'
down_revision: str | None = 'c020a36382f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'customer_display_preferences',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('timezone_name', sa.String(), nullable=False),
        sa.Column('theme', sa.Enum('SYSTEM', 'DARK', 'LIGHT', name='displaytheme', native_enum=False), nullable=False),
        sa.Column(
            'density', sa.Enum('COMFORTABLE', 'COMPACT', name='displaydensity', native_enum=False), nullable=False
        ),
        sa.Column('number_locale', sa.String(), nullable=False),
        sa.Column('view_currency', sa.String(), nullable=True),
        sa.Column('reduce_motion', sa.Enum('SYSTEM', 'ON', name='reducemotion', native_enum=False), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_customer_display_preferences_membership',
        ),
        sa.PrimaryKeyConstraint('tenant_id', 'user_id'),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("customer_display_preferences",))


def downgrade() -> None:
    op.drop_table('customer_display_preferences')
