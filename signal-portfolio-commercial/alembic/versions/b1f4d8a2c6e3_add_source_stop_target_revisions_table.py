"""add source_stop_target_revisions table

Revision ID: b1f4d8a2c6e3
Revises: a7c3f29d1e56
Create Date: 2026-10-01 00:00:00.000000

Track 41 (Gap 2, ADR-0011): the real, dedicated representation for a
`SourceEventKind.TARGET_UPDATE`/`STOP_UPDATE` revision --
`app/models/source_stop_target_revision.py`'s own module docstring has
the full design. Tenant-scoped AND append-only (same reasoning as
`ledger_entries`/`audit_events`), applied in the SAME transaction this
migration's own `create_table` runs in, pinned to only the table THIS
revision adds -- never the live, ever-growing
`app.db._TENANT_SCOPED_TABLES`/`_APPEND_ONLY_TABLES`, per
`04c418cbb547`'s own lesson.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_append_only, _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'b1f4d8a2c6e3'
down_revision: str | None = 'a7c3f29d1e56'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'source_stop_target_revisions',
        sa.Column('revision_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column(
            'kind', sa.Enum('TARGET_UPDATE', 'STOP_UPDATE', name='stoptargetrevisionkind', native_enum=False),
            nullable=False,
        ),
        sa.Column('source_event_native_key', sa.String(), nullable=False),
        sa.Column('instrument', sa.String(), nullable=True),
        sa.Column('stop_loss', sa.Numeric(28, 10), nullable=True),
        sa.Column('take_profit', sa.Numeric(28, 10), nullable=True),
        sa.Column('targets_json', sa.String(), nullable=False),
        sa.Column('resolved_source_entry_id', sa.String(), nullable=True),
        sa.Column('provider_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source_authority', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('revision_id'),
    )
    op.create_index(
        op.f('ix_source_stop_target_revisions_tenant_id'), 'source_stop_target_revisions', ['tenant_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_source_stop_target_revisions_source_event_native_key'), 'source_stop_target_revisions',
        ['source_event_native_key'], unique=False,
    )
    op.create_index(
        op.f('ix_source_stop_target_revisions_resolved_source_entry_id'), 'source_stop_target_revisions',
        ['resolved_source_entry_id'], unique=False,
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: module constants.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("source_stop_target_revisions",))
    _apply_append_only(connection, ("source_stop_target_revisions",))


def downgrade() -> None:
    op.drop_index(op.f('ix_source_stop_target_revisions_resolved_source_entry_id'), table_name='source_stop_target_revisions')
    op.drop_index(op.f('ix_source_stop_target_revisions_source_event_native_key'), table_name='source_stop_target_revisions')
    op.drop_index(op.f('ix_source_stop_target_revisions_tenant_id'), table_name='source_stop_target_revisions')
    op.drop_table('source_stop_target_revisions')
