"""Add signal_correlation_evidence table, signals.correlation_fingerprint,
and notification_bridge_events.needs_escalation/escalation_status (Track
12: cross-transport signal correlation/dedup + per-event completeness
escalation flag)

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30

See app/signal_correlation.py's own module docstring for the
`signal_correlation_evidence` table and `signals.correlation_fingerprint`
column's exact purpose (cross-transport fingerprint matching, composing
with -- never replacing -- the existing `channel_id`/`message_id`/
`revision_id` within-transport dedup added by 0020), and
`app/notification_bridge.py`'s module docstring plus
`SignalStore.list_notification_bridge_events_needing_escalation`'s own
docstring in app/db.py for `needs_escalation`/`escalation_status` (the
interface Track 13's active AI phone-retrieval escalation layer reads
and writes).

A fresh database gets all of this straight from app/db.py's `SCHEMA`
string and `_COLUMN_MIGRATIONS` list (`SignalStore`'s own bootstrap
re-runs `CREATE TABLE IF NOT EXISTS`/additive `ALTER TABLE` on every
open) -- same "this file is not the real source of truth for a
SignalStore-created database" precedent as every earlier numbered
revision's own docstring (see 0020's identical precedent for
`signals.channel_id`/`message_id`/`revision_id`, and 0024's for the
notification-bridge tables themselves). This revision only matters for
someone provisioning a database purely through the Alembic CLI, or an
operator running `alembic upgrade head` by hand against an existing
deployment stamped at 0026.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("correlation_fingerprint", sa.Text(), nullable=True))
    op.create_index("idx_signals_correlation_fingerprint", "signals", ["correlation_fingerprint"])

    op.add_column(
        "notification_bridge_events", sa.Column("needs_escalation", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("notification_bridge_events", sa.Column("escalation_status", sa.Text(), nullable=True))
    op.create_index(
        "idx_notification_bridge_events_escalation",
        "notification_bridge_events",
        ["needs_escalation", "escalation_status"],
    )

    op.create_table(
        "signal_correlation_evidence",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("canonical_signal_id", sa.Text(), nullable=False),
        sa.Column("evidence_signal_id", sa.Text(), nullable=False),
        sa.Column("fingerprint_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("side", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_signal_correlation_evidence_canonical", "signal_correlation_evidence", ["canonical_signal_id"])
    op.create_index("idx_signal_correlation_evidence_fp", "signal_correlation_evidence", ["fingerprint_key"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
