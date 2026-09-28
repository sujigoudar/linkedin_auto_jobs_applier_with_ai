"""add incidents table

Revision ID: e9a3c6f1d208
Revises: a1b2c3d4e5f6
Create Date: 2026-09-28 00:00:00.000000

The `incidents` table (app/models/incident.py) backing AD-21
"Commercial incidents and obligations". Plain, mutable, tenant-scoped
table -- unlike `support_cases` it is not FK'd to a single membership
(an incident can be reassigned across memberships over its life, and
`assignee_user_id` is validated at the service layer, not by a
compound FK), matching `products`/`research_runs`'s own precedent for
a tenant-scoped row that is not itself one user's record.

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
revision: str = 'e9a3c6f1d208'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'incidents',
        sa.Column('incident_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column(
            'service',
            sa.Enum('RIGHTS', 'PAYMENT', 'PUBLICATION', 'CUSTOMER_EXPOSURE', name='incidentservice', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'severity',
            sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='incidentseverity', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'state',
            sa.Enum('OPEN', 'ACKNOWLEDGED', 'ASSIGNED', 'RESOLVED', name='incidentstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('affected_object_type', sa.String(), nullable=True),
        sa.Column('affected_object_id', sa.String(), nullable=True),
        sa.Column('assignee_user_id', sa.String(), nullable=True),
        sa.Column('resolution_note', sa.String(), nullable=True),
        sa.Column('evidence_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('incident_id'),
    )
    op.create_index(op.f('ix_incidents_tenant_id'), 'incidents', ['tenant_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("incidents",))


def downgrade() -> None:
    op.drop_index(op.f('ix_incidents_tenant_id'), table_name='incidents')
    op.drop_table('incidents')
