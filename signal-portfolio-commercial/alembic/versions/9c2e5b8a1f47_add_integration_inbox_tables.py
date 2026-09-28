"""add export_stream_registrations and inbox_events tables

Revision ID: 9c2e5b8a1f47
Revises: 7a3f9c1d2e4b
Create Date: 2026-09-28 08:00:00.000000

The commercial half of the Signal Platform Integration Correction Pack's
own INTEGRATION_DECISION.md S4.4/S6 -- see app/models/integration_inbox.py
for what these two tables are and app/services/integration_inbox.py for
how they're used.

Applies row-level security in the SAME transaction this migration's own
`create_table` runs in (via `op.get_bind()`), pinned to only the two
tables THIS revision adds -- never the live, ever-growing
`app.db._TENANT_SCOPED_TABLES`, per `04c418cbb547`'s own lesson.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = '9c2e5b8a1f47'
down_revision: str | None = '7a3f9c1d2e4b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'export_stream_registrations',
        sa.Column('registration_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('source_stream', sa.String(), nullable=False),
        sa.Column('environment', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('registration_id'),
        sa.UniqueConstraint('source_stream', name='export_stream_registrations_source_stream_key'),
    )
    op.create_index(
        op.f('ix_export_stream_registrations_tenant_id'), 'export_stream_registrations', ['tenant_id'],
        unique=False,
    )

    op.create_table(
        'inbox_events',
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('source_stream', sa.String(), nullable=False),
        sa.Column('export_sequence', sa.Integer(), nullable=False),
        sa.Column('envelope_json', sa.String(), nullable=False),
        sa.Column('payload_hash', sa.String(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ledger_entry_id', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('event_id'),
        sa.UniqueConstraint('source_stream', 'export_sequence', name='uq_inbox_events_stream_sequence'),
    )
    op.create_index(op.f('ix_inbox_events_tenant_id'), 'inbox_events', ['tenant_id'], unique=False)

    #: Only the two tables THIS revision adds -- never the live,
    #: ever-growing `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ('export_stream_registrations', 'inbox_events'))


def downgrade() -> None:
    op.drop_index(op.f('ix_inbox_events_tenant_id'), table_name='inbox_events')
    op.drop_table('inbox_events')
    op.drop_index(op.f('ix_export_stream_registrations_tenant_id'), table_name='export_stream_registrations')
    op.drop_table('export_stream_registrations')
