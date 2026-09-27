"""add audit_events table

Revision ID: b36eae6aeb63
Revises: 745fc6555d73
Create Date: 2026-09-28 03:15:00.000000

The `audit_events` table (app/models/audit_event.py) backing AD-18
"Audit log and release evidence". Tenant-scoped AND append-only (like
ledger_entries/portfolio_versions) -- "Audit cannot be edited through
UI" is a database-level guarantee, not an application convention.

Applies row-level security AND the append-only trigger in the SAME
transaction this migration's own `create_table` runs in (via
`op.get_bind()`), both pinned to only the table THIS revision adds --
never the live, ever-growing `app.db._TENANT_SCOPED_TABLES`/
`_APPEND_ONLY_TABLES`, per `04c418cbb547`'s own lesson (now applied to
both helpers, not just the row-level-security one).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_append_only, _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'b36eae6aeb63'
down_revision: str | None = '745fc6555d73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'audit_events',
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('actor_user_id', sa.String(), nullable=False),
        sa.Column('object_type', sa.String(), nullable=False),
        sa.Column('object_id', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('outcome', sa.Enum('SUCCESS', 'DENIED', 'ERROR', name='auditoutcome', native_enum=False), nullable=False),
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index(op.f('ix_audit_events_tenant_id'), 'audit_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_audit_events_actor_user_id'), 'audit_events', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_audit_events_object_id'), 'audit_events', ['object_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: module constants.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("audit_events",))
    _apply_append_only(connection, ("audit_events",))


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_events_object_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_actor_user_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_tenant_id'), table_name='audit_events')
    op.drop_table('audit_events')
