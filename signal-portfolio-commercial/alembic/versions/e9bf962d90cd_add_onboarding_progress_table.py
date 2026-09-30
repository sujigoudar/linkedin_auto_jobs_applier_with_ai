"""add onboarding_progress table

Revision ID: e9bf962d90cd
Revises: 01e9924e74c6
Create Date: 2026-09-29 00:00:00.000000

The `onboarding_progress` table (app/models/onboarding_progress.py) --
the persisted, per-customer high-water mark for
`app/services/onboarding.py`'s own `OnboardingStage`/`advance()` state
machine. Before this table existed, that state machine was correct but
decorative: nothing persisted a customer's stage, and no call site
consulted it (see app/services/onboarding_progress.py's own docstring
for the real wiring this closes).

One row per (tenant_id, user_id), same shape as
`notification_preferences`/`eligibility_assessments`.

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
revision: str = 'e9bf962d90cd'
down_revision: str | None = '01e9924e74c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'onboarding_progress',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column(
            'stage',
            sa.Enum(
                'SIGNED_UP', 'EMAIL_VERIFIED', 'ELIGIBILITY_APPROVED', 'PRODUCT_SELECTED', 'PAYMENT_COMPLETED',
                'ENTITLEMENT_VERIFIED', 'ALERT_PREFERENCES_SET', 'PLATFORM_AUTHORIZED', 'MANDATE_PREVIEWED',
                'COPY_ACTIVATED', name='onboardingstage', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_onboarding_progress_membership',
        ),
        sa.PrimaryKeyConstraint('tenant_id', 'user_id'),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("onboarding_progress",))


def downgrade() -> None:
    op.drop_table('onboarding_progress')
