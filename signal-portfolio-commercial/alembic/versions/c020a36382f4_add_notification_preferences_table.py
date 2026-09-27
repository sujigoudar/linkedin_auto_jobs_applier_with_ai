"""add notification_preferences table

Revision ID: c020a36382f4
Revises: f6f6e1fc0793
Create Date: 2026-09-27 23:35:00.000000

The `notification_preferences` table (app/models/notification_preferences.py)
backing CU-12 "Alert delivery preferences". One row per (tenant_id,
user_id), like `EligibilityAssessment`'s own precedent.

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
revision: str = 'c020a36382f4'
down_revision: str | None = 'f6f6e1fc0793'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'notification_preferences',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('webhook_endpoint_id', sa.String(), nullable=True),
        sa.Column('categories', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('timezone_name', sa.String(), nullable=False),
        sa.Column('quiet_start', sa.String(), nullable=True),
        sa.Column('quiet_end', sa.String(), nullable=True),
        sa.Column('marketing_consent', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_notification_preferences_membership',
        ),
        sa.PrimaryKeyConstraint('tenant_id', 'user_id'),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("notification_preferences",))


def downgrade() -> None:
    op.drop_table('notification_preferences')
